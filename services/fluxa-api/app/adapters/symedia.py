from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests

from app.adapters.http_utils import safe_web_url, sanitize_text

TIMEOUT = 15

RULE_PROTECT_KEYWORDS = (
    "小于等于",
    "低分",
    "权重",
    "得分",
    "score",
    "weight",
    "已有更高",
    "更高版本",
    "跳过覆盖",
    "取消覆盖",
    "quality_score_lower",
    "quality_weight",
    "quality_higher_version",
    "quality_overwrite",
)

VERSION_REJECT_KEYWORDS = (
    "版本规则",
    "准入",
    "不满足规则",
    "规则跳过",
    "规则过滤",
    "quality_version_rule",
    "filtered",
)

AUTH_CONN_KEYWORDS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "认证失败",
    "登录失败",
    "token",
    "cookie",
    "connection",
    "timeout",
    "timed out",
    "连接失败",
    "连接超时",
    "网络异常",
)

IDENTIFY_FAIL_KEYWORDS = (
    "没有查询到媒体信息",
    "未查询到媒体信息",
    "查询不到媒体",
    "识别失败",
    "未识别",
    "未匹配",
    "tmdb",
    "刮削失败",
    "找不到媒体",
    "not found",
)


def classify_symedia_record(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (outcome, label, reason_text) purely from real error reason — NEVER by directory name or generic prefix.

    5 explicit failure/rejection categories:
      - weight_rejected (even if message starts with '文件转移错误', '小于等于/取消覆盖' => normal weight protection!)
      - version_rejected (version admission filter)
      - identify_failed (missing media info / TMDB match)
      - auth_or_connection_error (auth/network outage)
      - transfer_failed (real IO/file transfer failure)
    Plus: archived / replaced / unconfirmed.
    """
    raw_status = row.get("status")
    err_text = sanitize_text(row.get("errmsg") or row.get("message") or row.get("reason") or "")
    reason_code = str(row.get("reasonCode") or row.get("reason_code") or "").strip()
    haystack = f"{reason_code} {err_text}".casefold()

    src_score = (row.get("src_detail") or {}).get("totalScore") if isinstance(row.get("src_detail"), dict) else None
    dst_score = (row.get("dst_detail") or {}).get("totalScore") if isinstance(row.get("dst_detail"), dict) else None
    score_note = (
        f"（新文件权重 {src_score} vs 库内权重 {dst_score}）"
        if isinstance(src_score, (int, float)) and isinstance(dst_score, (int, float))
        else ""
    )

    if raw_status is True or raw_status == 1:
        if isinstance(src_score, (int, float)) and isinstance(dst_score, (int, float)) and src_score > dst_score:
            return "replaced", "归档并完成版本替换", f"已按 SA 规则替换旧版（{dst_score} → {src_score}）"
        return "archived", "归档成功", "已完成整理归档"

    if raw_status is False or raw_status == 0:
        # Check weight & version rejection BEFORE transfer_failed, because SA often prefixes them with '文件转移错误'!
        if any(k in haystack for k in RULE_PROTECT_KEYWORDS):
            return "rule_protected", "正常权重拒绝（保留库内版本）", (err_text or "未高于库内版本权重，已保留原版本") + score_note
        if any(k in haystack for k in VERSION_REJECT_KEYWORDS):
            return "version_rejected", "版本准入拒绝（规则过滤）", err_text or "不满足 SA 版本准入规则，已跳过"
        if any(k in haystack for k in IDENTIFY_FAIL_KEYWORDS):
            return "identify_failed", "SA 识别失败", err_text or "没有查询到媒体信息"
        if any(k in haystack for k in AUTH_CONN_KEYWORDS):
            return "auth_or_connection_error", "连接 / 认证故障", err_text or "SA 上游连接或认证异常"
        return "failed", "文件转移故障", err_text or "SA 文件转移失败"

    return "unconfirmed", "未确认", err_text or "SA 未返回明确状态"


class SymediaAdapter:
    """Symedia (SA) adapter — never modifies SA global rules or weights.

    Supports:
      - Read-only transfer history & transfer task list
      - Read-only OpenAPI capability probe (discovers whether manual identify/override params are supported)
      - Targeted single-file manual transfer (strictly 1 file, never full-library rescan)
    """

    def __init__(self, cfg: dict[str, Any], session: requests.Session | None = None):
        self.base_url = str(cfg.get("baseUrl") or "").strip().rstrip("/")
        self.web_url = str(cfg.get("webUrl") or "").strip().rstrip("/")
        self.token = str(cfg.get("token") or "").strip()
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.http = session or requests.Session()
        self._access_token = self.token

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self.token or (self.username and self.password)))

    def app_url(self, path: str = "") -> str:
        return safe_web_url(self.web_url, self.base_url, path)

    def _login(self) -> str:
        if self._access_token:
            return self._access_token
        resp = self.http.post(
            f"{self.base_url}/api/v1/login/access-token",
            data={"username": self.username, "password": self.password},
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Symedia 登录失败 ({resp.status_code})")
        body = resp.json() if resp.content else {}
        tok = str(body.get("access_token") or body.get("token") or "").strip()
        if not tok:
            raise RuntimeError("Symedia 登录未返回 access_token")
        self._access_token = tok
        return tok

    def _request(self, method: str, pathname: str, *, json_body: Any = None, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise RuntimeError("Symedia 未配置")
        tok = self._login()
        url = f"{self.base_url}/api/v1/{pathname.lstrip('/')}"
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        resp = self.http.request(method, url, headers=headers, json=json_body, params=params, timeout=TIMEOUT)
        if resp.status_code in {401, 403} and not self.token and self.username:
            self._access_token = ""
            tok = self._login()
            headers["Authorization"] = f"Bearer {tok}"
            resp = self.http.request(method, url, headers=headers, json=json_body, params=params, timeout=TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f"Symedia 响应异常 ({resp.status_code})")
        return resp.json() if resp.content else {}

    def _get(self, pathname: str) -> Any:
        return self._request("GET", pathname)

    def check_health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "未配置",
                "webUrl": self.app_url(),
            }
        try:
            records = self.list_transfer_history(count=50)
            fails = sum(1 for r in records if r["outcome"] in {"failed", "identify_failed"})
            msg = f"在线 · 最近 {len(records)} 条记录" + (f"，{fails} 条异常" if fails else "")
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": True,
                "connected": True,
                "status": "warn" if fails else "ok",
                "message": msg,
                "count": len(records),
                "webUrl": self.app_url(),
            }
        except Exception as exc:
            return {
                "service": "symedia",
                "name": "Symedia (SA)",
                "configured": True,
                "connected": False,
                "status": "error",
                "message": sanitize_text(exc),
                "webUrl": self.app_url(),
            }

    def list_transfer_tasks(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            data = self._get("transfer/transfer_list")
        except Exception:
            return []
        body = data.get("data") if isinstance(data, dict) and "data" in data else data
        rows = body
        if isinstance(body, dict):
            rows = next(
                (body.get(k) for k in ("items", "list", "transfer_list", "tasks") if isinstance(body.get(k), list)),
                [],
            )
        if not isinstance(rows, list):
            return []
        tasks: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("id") or row.get("transfer_task_id") or "").strip()
            if not tid:
                continue
            tasks.append({
                "id": tid,
                "name": str(row.get("name") or row.get("title") or f"归档任务 #{tid}").strip(),
                "sourcePath": str(row.get("source_path") or row.get("src") or "").strip(),
                "targetPath": str(row.get("target_path") or row.get("dest") or "").strip(),
                "failPath": str(row.get("fail_path") or row.get("failed_path") or "").strip(),
            })
        return tasks

    @staticmethod
    def _resolve_openapi_schema(schema: Any, components: dict[str, Any], depth: int = 0) -> dict[str, Any]:
        if depth > 6 or not isinstance(schema, dict):
            return {}
        if "$ref" in schema and isinstance(schema["$ref"], str):
            ref_name = schema["$ref"].rsplit("/", 1)[-1]
            target = (components.get("schemas") or {}).get(ref_name)
            return SymediaAdapter._resolve_openapi_schema(target, components, depth + 1)
        merged: dict[str, Any] = dict(schema)
        props: dict[str, Any] = dict(schema.get("properties") or {})
        for combiner in ("allOf", "anyOf", "oneOf"):
            for sub in schema.get(combiner) or []:
                res = SymediaAdapter._resolve_openapi_schema(sub, components, depth + 1)
                if isinstance(res.get("properties"), dict):
                    props.update(res["properties"])
                if res.get("type") == "array" and "items" in res and "items" not in merged:
                    merged["items"] = res["items"]
        if props:
            merged["properties"] = props
        return merged

    @staticmethod
    def _bind_param(candidates: tuple[str, ...], query_params: list[str], body_fields: list[str], item_fields: list[str]) -> dict[str, str] | None:
        q_map = {k.lower(): k for k in query_params}
        b_map = {k.lower(): k for k in body_fields}
        i_map = {k.lower(): k for k in item_fields}
        for key in candidates:
            lk = key.lower()
            if lk in i_map:
                return {"name": i_map[lk], "in": "item"}
            if lk in b_map:
                return {"name": b_map[lk], "in": "body"}
            if lk in q_map:
                return {"name": q_map[lk], "in": "query"}
        return None

    def probe_recovery_capabilities(self) -> dict[str, Any]:
        """Inspect SA OpenAPI spec (resolving $ref schemas) to verify REAL supported manual identify parameters.

        NEVER guesses or pretends unverified fields are supported by SA!
        """
        caps: dict[str, Any] = {
            "transferManual": False,
            "manualEndpointPath": "",
            "manualQueryParams": [],
            "manualBodyFields": [],
            "manualItemFields": [],
            "manualIdentifyParams": [],
            "supportsTmdbParam": False,
            "supportsSeasonEpisodeParam": False,
            "supportsExplicitIdentify": False,
            "paramBindings": {},
            "historyRedo": False,
            "discoveredIdentifyEndpoints": [],
            "openapiChecked": False,
        }
        if not self.configured:
            return caps
        headers = {"Accept": "application/json"}
        try:
            tok = self._login()
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        except Exception:
            pass

        for spec_path in ("/openapi.json", "/api/v1/openapi.json"):
            try:
                resp = self.http.get(f"{self.base_url}{spec_path}", headers=headers, timeout=6)
                if resp.status_code != 200:
                    continue
                spec = resp.json() if resp.content else {}
                paths = spec.get("paths") if isinstance(spec, dict) else {}
                components = spec.get("components") if isinstance(spec, dict) else {}
                if not isinstance(paths, dict):
                    continue
                caps["openapiChecked"] = True
                for p, ops in paths.items():
                    if not isinstance(ops, dict):
                        continue
                    op = ops.get("post") if isinstance(ops.get("post"), dict) else None
                    if not op:
                        continue
                    q_params = [
                        str(pr.get("name") or "")
                        for pr in (op.get("parameters") or [])
                        if isinstance(pr, dict) and pr.get("in") == "query" and pr.get("name")
                    ]
                    rb = ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
                    body_schema = self._resolve_openapi_schema(rb.get("schema") or {}, components)
                    body_props = body_schema.get("properties") if isinstance(body_schema.get("properties"), dict) else {}
                    body_fields = list(body_props.keys())
                    item_fields: list[str] = []
                    for nested_key in ("items", "fileitem", "file_item", "files"):
                        if nested_key in body_props:
                            sub = self._resolve_openapi_schema(body_props[nested_key], components)
                            if sub.get("type") == "array" and isinstance(sub.get("items"), dict):
                                sub = self._resolve_openapi_schema(sub["items"], components)
                            if isinstance(sub.get("properties"), dict):
                                item_fields.extend(k for k in sub["properties"].keys() if k not in item_fields)

                    all_fields_lower = {k.lower() for k in (*q_params, *body_fields, *item_fields)}
                    has_tmdb = any(k in all_fields_lower for k in ("tmdbid", "tmdb_id", "tmdbid_override"))
                    has_se = any(k in all_fields_lower for k in ("season", "season_num", "begin_season", "specify_season"))

                    if p.endswith("/transfer/manual"):
                        caps["transferManual"] = True
                        caps["manualEndpointPath"] = p
                        caps["manualQueryParams"] = q_params
                        caps["manualBodyFields"] = body_fields
                        caps["manualItemFields"] = item_fields
                        bindings: dict[str, dict[str, str]] = {}
                        for role, cands in (
                            ("tmdb", ("tmdbid", "tmdb_id", "tmdbId")),
                            ("mediaType", ("type", "media_type", "mediaType", "type_name")),
                            ("season", ("season", "season_num", "begin_season", "specify_season")),
                            ("episode", ("episode", "episode_num", "begin_episode", "specify_episode", "episode_detail")),
                        ):
                            b = self._bind_param(cands, q_params, body_fields, item_fields)
                            if b:
                                bindings[role] = b
                        caps["paramBindings"] = bindings
                        caps["supportsTmdbParam"] = "tmdb" in bindings
                        caps["supportsSeasonEpisodeParam"] = ("season" in bindings) and ("episode" in bindings)
                        caps["supportsExplicitIdentify"] = caps["supportsTmdbParam"] and caps["supportsSeasonEpisodeParam"]
                        caps["manualIdentifyParams"] = [f"{b['name']}({b['in']})" for b in bindings.values()]

                    if ("transfer" in p or "history" in p) and has_tmdb:
                        caps["discoveredIdentifyEndpoints"].append({
                            "path": p,
                            "queryParams": q_params,
                            "bodyFields": body_fields,
                            "itemFields": item_fields,
                            "hasSeason": has_se,
                        })
                    if "history/transfer" in p and ("redo" in p or "retry" in p or "manual" in p):
                        caps["historyRedo"] = True
                break
            except Exception:
                continue
        return caps

    def submit_manual_transfer(
        self,
        *,
        file_path: str,
        transfer_task_id: str,
        tmdb_id: str = "",
        media_type: str = "",
        season: int | None = None,
        episode: int | None = None,
        capabilities: dict[str, Any] | None = None,
        require_explicit_identify: bool = False,
    ) -> dict[str, Any]:
        """Submit strictly ONE file to SA's verified /api/v1/transfer/manual endpoint.

        Checks SA's real OpenAPI schema before sending TMDB/season/episode parameters:
        never silently sends unverified fields and pretends the override took effect.
        """
        clean_path = str(file_path or "").strip()
        tid = str(transfer_task_id or "").strip()
        if not clean_path.startswith("/") or not tid:
            raise ValueError("单文件归档需要完整的云盘/源文件路径与 SA 归档任务 ID")

        caps = capabilities if isinstance(capabilities, dict) else self.probe_recovery_capabilities()
        bindings = caps.get("paramBindings") if isinstance(caps.get("paramBindings"), dict) else {}
        needs_tmdb = bool(tmdb_id and str(tmdb_id).isdigit())
        needs_se = bool(media_type == "tv" and season is not None and int(season) > 0)

        if require_explicit_identify and caps.get("openapiChecked"):
            if (needs_tmdb and not caps.get("supportsTmdbParam")) or (needs_se and not caps.get("supportsSeasonEpisodeParam")):
                accepted = ", ".join(caps.get("manualBodyFields") or ["items", "transfer_task_id"])
                item_acc = ", ".join(caps.get("manualItemFields") or [])
                item_note = f"（items 字段：{item_acc}）" if item_acc else ""
                raise RuntimeError(
                    f"SA 真实接口 {caps.get('manualEndpointPath') or '/api/v1/transfer/manual'} 仅声明参数 [{accepted}]{item_note}，"
                    "不支持直接传入 TMDB ID / 季集覆盖；直接提交原文件名不会让指定映射生效。"
                    "请改用「保留规格规范改名（含 {tmdbid=...} 与目标季集）+ 单文件提交」，或在 SA 网页端手动指定识别。"
                )

        item_obj: dict[str, Any] = {"type": "file", "path": clean_path}
        payload: dict[str, Any] = {
            "items": [item_obj],
            "transfer_task_id": tid,
        }
        query_params: dict[str, Any] = {}
        applied_params: list[str] = []

        def _assign(role: str, value: Any) -> None:
            b = bindings.get(role)
            if not b:
                return
            loc, name = b.get("in"), b.get("name")
            if not name:
                return
            if loc == "item":
                item_obj[name] = value
            elif loc == "body":
                payload[name] = value
            elif loc == "query":
                query_params[name] = value
            applied_params.append(f"{name}={value}({loc})")

        if needs_tmdb:
            _assign("tmdb", int(tmdb_id))
        if media_type in {"movie", "tv"}:
            _assign("mediaType", "电视剧" if media_type == "tv" else "电影")
        if season is not None and int(season) > 0:
            _assign("season", int(season))
        if episode is not None and int(episode) > 0:
            _assign("episode", int(episode))

        data = self._request("POST", "transfer/manual", json_body=payload, params=query_params or None)
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(sanitize_text(data.get("message") or "Symedia 拒绝了单文件归档请求"))

        override_mode = "openapi_params" if applied_params else ("filename_tag" if "{tmdbid=" in clean_path.lower() else "unverified_no_openapi")
        return {
            "ok": True,
            "message": sanitize_text((data.get("message") if isinstance(data, dict) else "") or "已向 SA 提交单文件归档请求（待核验实际结果）"),
            "appliedParams": applied_params,
            "overrideMode": override_mode,
            "raw": data,
        }

    def list_transfer_history(self, count: int = 120) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        qs = urlencode({"page": 1, "count": max(1, min(300, count)), "sort_by": "date", "sort_order": "desc"})
        payload = self._get(f"history/transfer?{qs}")
        body = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        rows = body.get("list") if isinstance(body, dict) and isinstance(body.get("list"), list) else []
        records: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            outcome, label, reason = classify_symedia_record(row)
            raw_type = str(row.get("type") or row.get("media_type") or "").strip().lower()
            mtype = "tv" if raw_type in {"tv", "series", "电视剧", "剧集"} or row.get("seasons") or row.get("episodes") else "movie"
            tmdb_raw = str(row.get("tmdbid") or row.get("tmdb_id") or "").strip()
            tmdb_id = tmdb_raw if tmdb_raw.isdigit() and int(tmdb_raw) > 0 else ""
            src = str(row.get("src") or "").strip()
            dest = str(row.get("dest") or "").strip()
            season_ep = str(row.get("season_episode") or "").strip()
            seasons = str(row.get("seasons") or "").strip()
            episodes = str(row.get("episodes") or "").strip()
            if not seasons and not episodes and season_ep:
                seasons = season_ep
            records.append({
                "id": str(row.get("id") or "").strip(),
                "mediaKey": f"{mtype}:{tmdb_id}" if tmdb_id else "",
                "mediaType": mtype,
                "tmdbId": tmdb_id,
                "title": str(row.get("title") or "").strip() or (src.rsplit("/", 1)[-1] if src else "未识别文件"),
                "seasons": seasons,
                "episodes": episodes,
                "seasonEpisode": season_ep or f"{seasons}{episodes}",
                "src": src,
                "dest": dest,
                "fileName": src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if src else "",
                "fileSize": row.get("file_size") or row.get("size") or None,
                "outcome": outcome,
                "outcomeLabel": label,
                "reasonText": reason,
                "date": str(row.get("date") or "").strip(),
                "webUrl": self.app_url("history"),
            })
        return records
