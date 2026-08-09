from __future__ import annotations

import hashlib
import re

from app.task_public_runtime import safe_public_text


def _optional_nonnegative_integer(value):
    match = re.fullmatch(r"\d+", str(value if value is not None else "").strip())
    return int(match.group(0)) if match else None


def secupload_file_path_key(value) -> str:
    normalized = re.sub(
        r"/+",
        "/",
        str(value or "").strip().replace("\\", "/"),
    ).rstrip("/").casefold()
    if not normalized:
        return ""
    return hashlib.sha256(f"secupload-path\0{normalized}".encode("utf-8")).hexdigest()


def _failure_display_name(detail: dict, fallback="") -> str:
    values = (
        detail.get("file_name"),
        detail.get("path"),
        detail.get("relative_path"),
        detail.get("recorded_path"),
        fallback,
    )
    for value in values:
        raw = str(value or "").strip()
        if not raw or "://" in raw:
            continue
        name = re.split(r"[\\/]", raw)[-1].strip()
        if name:
            return safe_public_text(name, "未命名文件")[:160]
    return "未命名文件"


def _failure_error(detail: dict) -> tuple[str, str]:
    error = str(detail.get("last_error") or "").casefold()
    patterns = (
        (r"cookie|auth|unauthorized|forbidden|\b40[13]\b|登录|认证|凭据", "authentication_failed", "115 认证失败"),
        (r"timeout|timed out|network|connection|\bdns\b|网络|超时|连接", "network_failed", "网络连接失败"),
        (r"not found|missing|no such file|不存在|找不到|文件丢失", "file_missing", "源文件不可用"),
        (r"quota|storage|no space|容量|空间|配额", "storage_unavailable", "115 存储不可用"),
    )
    for pattern, category, label in patterns:
        if re.search(pattern, error, re.IGNORECASE):
            return category, label
    outcome = str(detail.get("outcome") or "").strip().casefold()
    if outcome == "sample_failed":
        return "instant_upload_failed", "秒传校验失败"
    if outcome == "pending_failed":
        return "retry_failed", "重试后仍失败"
    return "upload_failed", "秒传失败"


def _result_payloads(value) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _structured_run_counts(result) -> dict:
    payloads = [
        row for row in _result_payloads(result)
        if _optional_nonnegative_integer(row.get("success_count")) is not None
        or _optional_nonnegative_integer(row.get("failed_count")) is not None
    ]
    if not payloads:
        return {"success": None, "failed": None}

    def total(key):
        values = [_optional_nonnegative_integer(row.get(key)) for row in payloads]
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    return {"success": total("success_count"), "failed": total("failed_count")}


def parse_secupload_run_counts(message: str, result=None) -> dict:
    text = str(message or "")
    success = re.search(r"成功\s*(\d+)\s*个", text)
    failed = re.search(r"失败\s*(\d+)\s*个", text)
    structured = _structured_run_counts(result)
    return {
        "success": (
            structured["success"]
            if structured["success"] is not None
            else int(success.group(1)) if success else None
        ),
        "failed": (
            structured["failed"]
            if structured["failed"] is not None
            else int(failed.group(1)) if failed else None
        ),
    }


def _failure_detail_rows(result) -> list[tuple[dict, str]]:
    rows = []
    detail_fields = {
        "file_name", "path", "relative_path", "recorded_path", "outcome",
        "attempts", "last_error", "last_attempt_at", "source_relative_path",
    }
    for payload in _result_payloads(result):
        value = payload.get("failure_details")
        if value is None and set(payload) & detail_fields:
            value = payload
        if isinstance(value, list):
            rows.extend((row, "") for row in value if isinstance(row, dict))
        elif isinstance(value, dict) and set(value) & detail_fields:
            rows.append((value, ""))
        elif isinstance(value, dict):
            rows.extend((row, str(key)) for key, row in value.items() if isinstance(row, dict))
    return rows


def _failure_file(detail, fallback, context):
    raw_path = next((
        str(detail.get(key) or "").strip()
        for key in ("path", "relative_path", "recorded_path", "source_relative_path")
        if str(detail.get(key) or "").strip()
    ), fallback)
    display_name = _failure_display_name(detail, fallback)
    path_key = secupload_file_path_key(raw_path)
    identity = path_key or hashlib.sha256(display_name.casefold().encode("utf-8")).hexdigest()
    file_key = hashlib.sha256(
        f"secupload-file\0{context['targetItemId']}\0{identity}".encode("utf-8")
    ).hexdigest()
    category, label = _failure_error(detail)
    return {
        "fileKey": file_key,
        "batchKey": context["batchKey"],
        "targetItemId": context["targetItemId"],
        "pathKey": path_key,
        "displayName": display_name,
        "errorCategory": category,
        "errorLabel": label,
        "retryCount": _optional_nonnegative_integer(detail.get("attempts")),
        "observedAt": str(detail.get("last_attempt_at") or context["observedAt"] or ""),
    }


def _prefer_candidate(current, candidate) -> bool:
    if current is None:
        return True
    current_retry = current.get("retryCount")
    candidate_retry = candidate.get("retryCount")
    return candidate_retry is not None and (current_retry is None or candidate_retry > current_retry)


def parse_secupload_failure_files(
    result,
    *,
    target_item_id: str,
    batch_key: str,
    observed_at: str,
) -> list[dict]:
    deduped = {}
    context = {
        "targetItemId": target_item_id,
        "batchKey": batch_key,
        "observedAt": observed_at,
    }
    for detail, fallback in _failure_detail_rows(result):
        candidate = _failure_file(detail, fallback, context)
        if _prefer_candidate(deduped.get(candidate["fileKey"]), candidate):
            deduped[candidate["fileKey"]] = candidate
    return sorted(
        deduped.values(),
        key=lambda row: (row["displayName"].casefold(), row["fileKey"]),
    )


def _success_detail_rows(result) -> list[tuple[dict, str]]:
    rows = []
    detail_fields = {
        "file_name", "path", "relative_path", "recorded_path", "source_relative_path",
        "uploaded_at", "completed_at", "finished_at",
    }
    for payload in _result_payloads(result):
        value = next((payload.get(key) for key in (
            "success_details", "successful_files", "uploaded_files", "completed_files",
        ) if payload.get(key) is not None), None)
        if value is None and payload.get("outcome") in {"success", "succeeded", "uploaded", "completed"}:
            value = payload
        if isinstance(value, list):
            rows.extend((row, "") for row in value if isinstance(row, dict))
        elif isinstance(value, dict) and set(value) & detail_fields:
            rows.append((value, ""))
        elif isinstance(value, dict):
            rows.extend((row, str(key)) for key, row in value.items() if isinstance(row, dict))
    return rows


def _success_file(detail, fallback, context):
    raw_path = next((
        str(detail.get(key) or "").strip()
        for key in ("path", "relative_path", "recorded_path", "source_relative_path")
        if str(detail.get(key) or "").strip()
    ), fallback)
    display_name = _failure_display_name(detail, fallback)
    path_key = secupload_file_path_key(raw_path)
    identity = path_key or hashlib.sha256(display_name.casefold().encode("utf-8")).hexdigest()
    file_key = hashlib.sha256(
        f"secupload-file\0{context['targetItemId']}\0{identity}".encode("utf-8")
    ).hexdigest()
    return {
        "fileKey": file_key,
        "batchKey": context["batchKey"],
        "targetItemId": context["targetItemId"],
        "pathKey": path_key,
        "displayName": display_name,
        "observedAt": str(
            detail.get("uploaded_at")
            or detail.get("completed_at")
            or detail.get("finished_at")
            or context["observedAt"]
            or ""
        ),
    }


def parse_secupload_success_files(
    result,
    *,
    target_item_id: str,
    batch_key: str,
    observed_at: str,
) -> list[dict]:
    deduped = {}
    context = {
        "targetItemId": target_item_id,
        "batchKey": batch_key,
        "observedAt": observed_at,
    }
    for detail, fallback in _success_detail_rows(result):
        candidate = _success_file(detail, fallback, context)
        if candidate["fileKey"]:
            deduped[candidate["fileKey"]] = candidate
    return sorted(
        deduped.values(),
        key=lambda row: (row["displayName"].casefold(), row["fileKey"]),
    )


def merge_secupload_failure_files(values) -> list[dict]:
    deduped = {}
    for row in values:
        if not isinstance(row, dict) or not row.get("fileKey"):
            continue
        if _prefer_candidate(deduped.get(row["fileKey"]), row):
            deduped[row["fileKey"]] = row
    return sorted(
        deduped.values(),
        key=lambda row: (str(row.get("displayName") or "").casefold(), row["fileKey"]),
    )
