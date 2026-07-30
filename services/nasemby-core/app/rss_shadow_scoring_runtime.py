from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata


SINGLE_MATCH_GROUPS = (
    "videoFormat",
    "audioCodec",
    "videoCodec",
    "color_depth",
    "frame_rate",
    "dolby_vision",
    "dynamic_range",
    "enhancement",
    "resource_type",
    "release_type",
    "streaming_service",
    "releaseGroup",
    "subtitle",
    "file_extension",
)
SUPPORTED_VERSION_ATTRIBUTES = {
    "videoFormat",
    "resource_type",
    "release_type",
    "streaming_service",
    "file_extension",
}


class ShadowScoringUnsupported(ValueError):
    def __init__(self, code):
        super().__init__(str(code or "shadow_scoring_unsupported"))
        self.code = str(code or "shadow_scoring_unsupported")


def _text(value):
    return str(value or "").strip()


def _normalized(value):
    value = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _number(value, code):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShadowScoringUnsupported(code)
    result = float(value)
    if not math.isfinite(result):
        raise ShadowScoringUnsupported(code)
    return result


def stable_payload_hash(payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def rss_artifact_key(item):
    fingerprint = _text((item or {}).get("fingerprint"))
    if not fingerprint:
        return ""
    return f"rss:{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:32]}"


def rss_target_key(item):
    item = item if isinstance(item, dict) else {}
    media_type = _text(item.get("media_type") or item.get("mediaType")).lower()
    tmdb_id = _text(item.get("tmdb_id") or item.get("tmdbId"))
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return ""
    if media_type == "movie":
        return f"movie:tmdb:{tmdb_id}"
    try:
        season = int(item.get("season_number", item.get("seasonNumber")))
        episode_start = int(item.get("episode_start", item.get("episodeStart")))
        episode_end = int(
            item.get("episode_end", item.get("episodeEnd"))
            or episode_start
        )
    except (TypeError, ValueError):
        return ""
    if season <= 0 or episode_start <= 0 or episode_end < episode_start:
        return ""
    return (
        f"tv:tmdb:{tmdb_id}:season:{season}:"
        f"episodes:{episode_start}-{episode_end}"
    )


def _rule_categories(rule):
    result = []
    values = rule.get("category") if isinstance(rule, dict) else []
    if isinstance(values, str):
        values = [values]
    for value in values if isinstance(values, list) else []:
        text = _text(value)
        if not text:
            continue
        result.append(text.split("::", 1)[-1])
    return result


def _subscription_categories(subscription):
    subscription = subscription if isinstance(subscription, dict) else {}
    explicit = []
    for field in (
        "media_category",
        "mediaCategory",
        "resolved_category",
        "resolvedCategory",
        "category",
    ):
        values = subscription.get(field)
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            explicit.extend(_text(value).split("::", 1)[-1] for value in values)
    explicit = [_normalized(value) for value in explicit if _normalized(value)]
    if explicit:
        return explicit, "explicit"

    save_path = _text(
        subscription.get("save_path")
        or subscription.get("savePath")
        or subscription.get("download_path")
        or subscription.get("downloadPath")
    ).replace("\\", "/").rstrip("/")
    basename = save_path.rsplit("/", 1)[-1] if save_path else ""
    normalized = _normalized(basename)
    return ([normalized] if normalized else []), "save_path"


def _rule_matches_subscription(rule, media_type, categories, category_source):
    if rule.get("enabled") is False:
        return False
    if _text(rule.get("media_type") or rule.get("mediaType")).lower() != media_type:
        return False
    labels = [_normalized(value) for value in _rule_categories(rule)]
    if category_source == "explicit":
        return any(category == label for category in categories for label in labels if label)
    return any(category.endswith(label) for category in categories for label in labels if label)


def select_subscription_rule(rules, subscription):
    subscription = subscription if isinstance(subscription, dict) else {}
    rules = [rule for rule in rules if isinstance(rule, dict)]
    explicit_rule_id = _text(
        subscription.get("meta_weight_rule_id")
        or subscription.get("metaWeightRuleId")
        or subscription.get("rule_id")
    )
    if explicit_rule_id:
        matches = [rule for rule in rules if _text(rule.get("id")) == explicit_rule_id]
        return (matches[0], "") if len(matches) == 1 else (None, "rule_not_found")

    media_type = _text(
        subscription.get("media_type")
        or subscription.get("mediaType")
        or subscription.get("type")
    ).lower()
    if media_type not in {"movie", "tv"}:
        return None, "subscription_media_type_unconfirmed"
    categories, source = _subscription_categories(subscription)
    if not categories:
        return None, "subscription_category_unconfirmed"

    matches = [
        rule
        for rule in rules
        if _rule_matches_subscription(rule, media_type, categories, source)
    ]
    if not matches:
        return None, "rule_not_found"
    if len(matches) > 1:
        return None, "rule_ambiguous"
    return matches[0], ""


def _regex_matches(pattern, corpus):
    pattern = _text(pattern)
    if not pattern:
        raise ShadowScoringUnsupported("rule_pattern_missing")
    try:
        return bool(re.search(pattern, corpus, re.IGNORECASE))
    except re.error as exc:
        raise ShadowScoringUnsupported("rule_pattern_invalid") from exc


def _rule_entries(group):
    if isinstance(group, list):
        return [(str(index), item) for index, item in enumerate(group) if isinstance(item, dict)]
    if isinstance(group, dict):
        return [
            (str(key), item)
            for key, item in group.items()
            if key not in {"blacklist", "whitelist"} and isinstance(item, dict)
        ]
    if group in (None, ""):
        return []
    raise ShadowScoringUnsupported("rule_group_invalid")


def _filters(rule, field, group):
    blacklist = []
    whitelist = []
    if isinstance(group, dict):
        blacklist.extend(group.get("blacklist") or [])
        whitelist.extend(group.get("whitelist") or [])
    blacklist.extend(rule.get(f"{field}_blacklist") or [])
    whitelist.extend(rule.get(f"{field}_whitelist") or [])
    if not isinstance(blacklist, list) or not isinstance(whitelist, list):
        raise ShadowScoringUnsupported("rule_filter_invalid")
    return blacklist, whitelist


def _filter_allows(rule, field, group, corpus):
    blacklist, whitelist = _filters(rule, field, group)
    if any(_regex_matches(pattern, corpus) for pattern in blacklist):
        return False
    return not whitelist or any(_regex_matches(pattern, corpus) for pattern in whitelist)


def _score_group(rule, field, corpus, sum_matches=False):
    weight = _number(rule.get(f"{field}_weight", 1), "rule_weight_invalid")
    if weight == 0:
        return 0.0, [], []
    group = rule.get(field)
    if not _filter_allows(rule, field, group, corpus):
        return 0.0, [], []
    matches = []
    aliases = []
    for key, entry in _rule_entries(group):
        if not _regex_matches(entry.get("pattern"), corpus):
            continue
        score = _number(entry.get("score"), "rule_score_invalid")
        label = _text(entry.get("name")) or key
        matches.append({"field": field, "label": label, "score": score * weight})
        aliases.extend((key, label))
        if not sum_matches:
            break
    return sum(value["score"] for value in matches), matches, aliases


def _condition_matches(condition, facts):
    if not isinstance(condition, dict):
        raise ShadowScoringUnsupported("version_condition_invalid")
    attribute = _text(condition.get("attribute"))
    if attribute not in SUPPORTED_VERSION_ATTRIBUTES:
        raise ShadowScoringUnsupported("version_attribute_unsupported")
    if _text(condition.get("match_mode") or "any").lower() != "any":
        raise ShadowScoringUnsupported("version_match_mode_unsupported")
    values = condition.get("values")
    if values is None and condition.get("value") not in (None, ""):
        values = [condition.get("value")]
    if not isinstance(values, list) or not values:
        raise ShadowScoringUnsupported("version_condition_values_missing")
    aliases = {_normalized(value) for value in facts.get(attribute, []) if _normalized(value)}
    if not aliases:
        return None
    expected = {_normalized(value) for value in values if _normalized(value)}
    if not expected:
        raise ShadowScoringUnsupported("version_condition_values_invalid")
    return bool(aliases & expected)


def _version_entry_decision(entry, facts):
    if not isinstance(entry, dict) or _text(entry.get("kind") or "local") != "local":
        raise ShadowScoringUnsupported("version_entry_unsupported")
    version = entry.get("version")
    if not isinstance(version, dict):
        raise ShadowScoringUnsupported("version_entry_invalid")
    include_results = [
        _condition_matches(condition, facts)
        for condition in version.get("include_conditions") or []
    ]
    exclude_results = [
        _condition_matches(condition, facts)
        for condition in version.get("exclude_conditions") or []
    ]
    results = (*include_results, *exclude_results)
    if any(result is False for result in include_results) or any(
        result is True for result in exclude_results
    ):
        return "skipped", ""
    if any(result is None for result in results):
        return "unknown", ""
    return "accepted", _text(version.get("name"))


def _version_control_decision(rule, facts):
    if rule.get("version_control_enabled") is not True:
        return "accepted", ""
    entries = rule.get("version_control_entries")
    if not isinstance(entries, list) or not entries:
        raise ShadowScoringUnsupported("version_entries_missing")
    had_unknown = False
    for entry in entries:
        decision, version_name = _version_entry_decision(entry, facts)
        had_unknown = had_unknown or decision == "unknown"
        if decision == "accepted":
            return decision, version_name
    if had_unknown:
        raise ShadowScoringUnsupported("version_fields_unconfirmed")
    return "rejected", ""


def _score_file_size(rule, item):
    weight = _number(rule.get("file_size_weight", 1), "rule_weight_invalid")
    if not weight:
        return 0.0, []
    try:
        size_bytes = int(item.get("size_bytes", item.get("sizeBytes")) or 0)
    except (TypeError, ValueError) as exc:
        raise ShadowScoringUnsupported("candidate_size_invalid") from exc
    if size_bytes <= 0:
        raise ShadowScoringUnsupported("candidate_size_unconfirmed")
    weighted = _number(rule.get("file_size_score"), "rule_score_invalid") * weight
    return weighted, [{"field": "file_size", "label": "file_size", "score": weighted}]


def score_rss_candidate(rule, item):
    rule = rule if isinstance(rule, dict) else {}
    item = item if isinstance(item, dict) else {}
    title = _text(item.get("title"))
    if not title:
        raise ShadowScoringUnsupported("candidate_title_missing")
    corpus = " ".join(
        value
        for value in (
            title,
            _text(item.get("version_summary") or item.get("versionSummary")),
            _text(item.get("category")),
        )
        if value
    )
    total = 0.0
    breakdown = []
    facts = {}
    for field in SINGLE_MATCH_GROUPS:
        value, rows, aliases = _score_group(rule, field, corpus)
        total += value
        breakdown.extend(rows)
        facts[field] = aliases
    value, rows, aliases = _score_group(rule, "custom_attributes", corpus, sum_matches=True)
    total += value
    breakdown.extend(rows)
    facts["custom_attributes"] = aliases

    file_size_score, file_size_rows = _score_file_size(rule, item)
    total += file_size_score
    breakdown.extend(file_size_rows)

    always_override_weight = _number(
        rule.get("always_override_weight", 0),
        "rule_weight_invalid",
    )
    if always_override_weight:
        raise ShadowScoringUnsupported("always_override_unsupported")

    version_state, version_name = _version_control_decision(rule, facts)
    return {
        "score": round(total, 4),
        "breakdown": breakdown,
        "versionState": version_state,
        "versionName": version_name,
    }
