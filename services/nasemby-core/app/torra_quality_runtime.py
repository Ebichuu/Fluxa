from __future__ import annotations

import math
from urllib.parse import quote

from app.torra_read_runtime import TorraReadClient, TorraReadConfig


TORRA_JOB_STATUSES = {"pending", "running", "success", "failed", "cancelled"}


class TorraQualityError(RuntimeError):
    pass


class TorraQualityBlocked(TorraQualityError):
    code = "TORRA_RESPONSE_BLOCKED"
    state = "blocked"


def _blocked(message):
    raise TorraQualityBlocked(message)


def _success_data(payload, operation):
    if not isinstance(payload, dict):
        _blocked(f"Torra {operation}响应结构无效")
    if payload.get("success") is False:
        raise TorraQualityError(f"Torra {operation}未成功")
    if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
        _blocked(f"Torra {operation}响应结构无效")
    return payload["data"]


def _required_text(mapping, field, context):
    value = mapping.get(field) if isinstance(mapping, dict) else None
    text = str(value or "").strip()
    if not text:
        _blocked(f"Torra {context}缺少 {field}")
    return text


def _score(mapping, field, context):
    value = mapping.get(field) if isinstance(mapping, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _blocked(f"Torra {context}字段 {field} 无效")
    number = float(value)
    if not math.isfinite(number):
        _blocked(f"Torra {context}字段 {field} 无效")
    return number


def _selected_candidate_map(selected_candidates):
    if not isinstance(selected_candidates, dict) or not selected_candidates:
        raise ValueError("Torra 下载至少需要一个已选择候选")
    selected = {}
    for row_id, candidate_id in selected_candidates.items():
        row_key = str(row_id or "").strip()
        candidate_key = str(candidate_id or "").strip()
        if not row_key or not candidate_key:
            raise ValueError("Torra 候选映射包含空 ID")
        selected[row_key] = candidate_key
    return selected


def _analysis_rows(job):
    if not isinstance(job, dict) or job.get("status") != "success":
        _blocked("Torra 分析 job 尚未成功")
    result = job.get("result")
    if not isinstance(result, dict):
        _blocked("Torra 分析结果结构无效")
    analysis_id = _required_text(result, "analysis_id", "分析结果")
    rows = result.get("rows")
    if not isinstance(rows, list):
        _blocked("Torra 分析结果缺少 rows")
    return analysis_id, rows


def _candidate_fields(candidate):
    if not isinstance(candidate, dict):
        _blocked("Torra 候选结构无效")
    candidate_id = _required_text(candidate, "candidate_id", "候选")
    is_upgrade = candidate.get("is_upgrade")
    if not isinstance(is_upgrade, bool):
        _blocked("Torra 候选字段 is_upgrade 无效")
    return candidate_id, is_upgrade, _score(candidate, "meta_weight_score", "候选")


def _best_row_candidate(row):
    if not isinstance(row, dict):
        _blocked("Torra 分析行结构无效")
    row_id = _required_text(row, "row_id", "分析行")
    library_score = _score(row, "library_meta_weight_score", "分析行")
    candidates = row.get("candidates")
    if not isinstance(candidates, list):
        _blocked("Torra 分析行缺少 candidates")
    best = None
    best_score = None
    for candidate in candidates:
        candidate_id, is_upgrade, candidate_score = _candidate_fields(candidate)
        if not is_upgrade or candidate_score <= library_score:
            continue
        if best_score is None or candidate_score > best_score:
            best = candidate_id
            best_score = candidate_score
    return row_id, best


class TorraQualityClient(TorraReadClient):
    def __init__(self, config: TorraReadConfig, session=None, clock=None):
        super().__init__(config, session=session, clock=clock)

    def _submit_job(self, pathname, payload, operation):
        if not self.is_configured():
            raise TorraQualityError("未配置 Torra 地址或认证信息")
        data = _success_data(self._write_json(pathname, payload), operation)
        return _required_text(data, "job_id", f"{operation}响应")

    def submit_analysis(self, subscription_id):
        subscription_id = str(subscription_id or "").strip()
        if not subscription_id:
            raise ValueError("Torra 订阅 ID 不能为空")
        pathname = f"/api/v1/subscriptions/rewash/{quote(subscription_id, safe='')}"
        return self._submit_job(pathname, None, "洗版分析")

    def submit_download(self, subscription_id, analysis_id, selected_candidates):
        subscription_id = str(subscription_id or "").strip()
        analysis_id = str(analysis_id or "").strip()
        if not subscription_id or not analysis_id:
            raise ValueError("Torra 订阅 ID 和分析 ID 不能为空")
        selected = _selected_candidate_map(selected_candidates)
        pathname = f"/api/v1/subscriptions/rewash/{quote(subscription_id, safe='')}/download"
        return self._submit_job(pathname, {
            "analysis_id": analysis_id,
            "selected_candidates": selected,
            "force_push": True,
        }, "洗版下载")

    def get_job(self, job_id):
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("Torra job ID 不能为空")
        if not self.is_configured():
            raise TorraQualityError("未配置 Torra 地址或认证信息")
        status_code, payload = self._fetch_json(f"/api/v1/jobs/{quote(job_id, safe='')}")
        if status_code in {401, 403}:
            raise TorraQualityError("Torra Token 无效或已过期")
        if status_code >= 400:
            raise TorraQualityError(f"Torra job 查询失败：{status_code}")
        data = _success_data(payload, "job 查询")
        status = str(data.get("status") or "").strip().lower()
        if status not in TORRA_JOB_STATUSES:
            _blocked("Torra job 状态无效")
        result = data.get("result")
        if status == "success" and not isinstance(result, dict):
            _blocked("Torra job 成功响应缺少 result")
        return {
            "job_id": job_id,
            "status": status,
            "result": result if status == "success" else None,
        }

    @staticmethod
    def select_upgrade_candidates(job):
        analysis_id, rows = _analysis_rows(job)
        selected = {}
        for row in rows:
            row_id, best = _best_row_candidate(row)
            if best:
                selected[row_id] = best
        return {
            "analysis_id": analysis_id,
            "selected_candidates": selected,
            "row_count": len(rows),
            "selected_count": len(selected),
        }
