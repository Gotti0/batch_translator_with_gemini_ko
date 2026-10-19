"""
Gemini Batch API Client for Neo Batch Translator (BTG)

Gemini Batch API(`client.aio.batches`)와 File API 호출을 한곳에 모은 얇은 래퍼입니다.
SDK의 배치 필드명이 바뀌어도 이 파일만 고치면 되도록, 상위 계층에는 SDK 타입 대신
`BatchJobInfo`·`BatchItemResult`만 돌려줍니다.

배치 작업과 업로드 파일은 제출한 API 키의 프로젝트에만 보이므로, 이 클라이언트는
키 하나에 고정됩니다. 실시간 호출의 키 로테이션은 쓰지 않습니다.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from core.exceptions import (
    BtgApiClientException,
    BtgApiInvalidRequestException,
    BtgApiRateLimitException,
)
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

FREE_TIER_HINT = "무료 티어 키는 Batch API를 사용할 수 없습니다. 결제가 설정된 유료 키를 '배치용 API 키'에 입력하세요."


def _quota_type(metric: str, quota_id: str) -> str:
    # 제출 방식만으로 인라인 할당량 초과라고 추정하지 않고 서버의 식별자만 해석한다.
    identifier = re.sub(r"[^a-z0-9]", "", f"{metric} {quota_id}".lower())
    if "enqueued" in identifier and "token" in identifier:
        return "BATCH_ENQUEUED_TOKENS"
    if "inline" in identifier and "batch" in identifier:
        return "INLINE_BATCH"
    if "batch" in identifier:
        return "BATCH_OTHER"
    if "perminute" in identifier:
        if "token" in identifier:
            return "TPM"
        if "request" in identifier:
            return "RPM"
    if "perday" in identifier:
        if "token" in identifier:
            return "TPD"
        if "request" in identifier:
            return "RPD"
    return "UNKNOWN"


def _quota_diagnostics(error: Exception) -> dict:
    body = getattr(error, "details", None)
    inner = body.get("error", body) if isinstance(body, dict) else {}
    details = inner.get("details", []) if isinstance(inner, dict) else []
    result = {"violations": [], "error_info": [], "retry_delay": None, "response_headers": {}}
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", "")).rsplit("/", 1)[-1]
        if kind == "google.rpc.QuotaFailure":
            violations = detail.get("violations")
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                result["violations"].append({
                    key: violation[key] for key in (
                        "quotaMetric", "quotaId", "quotaValue", "quotaDimensions",
                    ) if key in violation
                })
        elif kind == "google.rpc.RetryInfo":
            result["retry_delay"] = detail.get("retryDelay")
        elif kind == "google.rpc.ErrorInfo":
            metadata = detail.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            result["error_info"].append({
                "reason": detail.get("reason"), "domain": detail.get("domain"),
                "metadata": {key: metadata[key] for key in (
                    "quota_metric", "quota_limit", "quota_limit_value", "quota_location", "service",
                ) if key in metadata},
            })
            if metadata.get("quota_metric") or metadata.get("quota_limit"):
                result["violations"].append({
                    "quotaMetric": metadata.get("quota_metric"),
                    "quotaId": metadata.get("quota_limit"),
                    "quotaValue": metadata.get("quota_limit_value"),
                })
    for violation in result["violations"]:
        violation["type"] = _quota_type(
            str(violation.get("quotaMetric") or ""), str(violation.get("quotaId") or ""),
        )
        identifier = re.sub(r"[^a-z0-9]", "", str(violation.get("quotaId") or "").lower())
        violation["period"] = "day" if "perday" in identifier else "minute" if "perminute" in identifier else "unspecified"
    result["quota_types"] = sorted({v["type"] for v in result["violations"]}) or ["UNKNOWN"]
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is not None:
        # 요청 헤더와 본문에는 키·원문이 있으므로 응답의 추적 정보만 허용한다.
        for name in ("retry-after", "x-request-id", "x-goog-request-id"):
            value = headers.get(name)
            if isinstance(value, str):
                result["response_headers"][name] = value
    return result

# 응답이 비었을 때 검열로 보는 finish_reason
_BLOCKED_FINISH_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


class BatchJobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (
            BatchJobState.SUCCEEDED,
            BatchJobState.PARTIALLY_SUCCEEDED,
            BatchJobState.FAILED,
            BatchJobState.CANCELLED,
            BatchJobState.EXPIRED,
        )


_STATE_MAP = {
    "JOB_STATE_QUEUED": BatchJobState.PENDING,
    "JOB_STATE_PENDING": BatchJobState.PENDING,
    "JOB_STATE_PAUSED": BatchJobState.PENDING,
    "JOB_STATE_RUNNING": BatchJobState.RUNNING,
    "JOB_STATE_UPDATING": BatchJobState.RUNNING,
    "JOB_STATE_CANCELLING": BatchJobState.RUNNING,
    "JOB_STATE_SUCCEEDED": BatchJobState.SUCCEEDED,
    "JOB_STATE_PARTIALLY_SUCCEEDED": BatchJobState.PARTIALLY_SUCCEEDED,
    "JOB_STATE_FAILED": BatchJobState.FAILED,
    "JOB_STATE_CANCELLED": BatchJobState.CANCELLED,
    "JOB_STATE_EXPIRED": BatchJobState.EXPIRED,
}


def map_job_state(raw_state: Any) -> BatchJobState:
    """SDK의 `JobState`(enum 또는 문자열)를 내부 상태로 바꾼다."""
    name = getattr(raw_state, "name", None) or str(raw_state or "")
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return _STATE_MAP.get(name, BatchJobState.UNKNOWN)


@dataclass
class BatchItemResult:
    """배치 요청 하나의 결과. `text`가 있으면 성공, 없으면 `error` 또는 `blocked`."""
    key: Optional[str]
    text: Optional[str] = None
    error: Optional[str] = None
    blocked: bool = False
    finish_reason: Optional[str] = None


@dataclass
class BatchJobInfo:
    name: str
    display_name: Optional[str]
    state: BatchJobState
    raw_state: str = ""
    error_message: Optional[str] = None
    successful_count: Optional[int] = None
    failed_count: Optional[int] = None
    results: Optional[List[BatchItemResult]] = field(default=None, repr=False)
    estimated_input_tokens: Optional[int] = None


def key_fingerprint(api_key: str) -> str:
    """키를 저장하지 않고 같은 키인지 확인하기 위한 지문."""
    return "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def to_serializable_response_schema(schema: Any) -> Any:
    """응답 스키마를 요청에 담을 수 있는 `Schema` 객체로 바꾼다.

    실시간 호출은 SDK가 보내는 순간 파이썬 타입(`list[TranslatedUnit]`)을 `Schema`로 바꾸지만, 배치는
    요청을 먼저 만들어 크기를 재고 묶는다. 파이썬 타입은 그 직렬화에서 실패하므로 미리 바꿔 둔다.
    변환은 SDK가 쓰는 함수 그대로다. 클라이언트 인자는 개발자 API에서 쓸 수 없는 속성 검사에만 쓰인다.
    """
    if schema is None or isinstance(schema, genai_types.Schema):
        return schema
    from google.genai import _transformers
    return _transformers.t_schema(None, schema)


def _parse_response(key: Optional[str], response: Any) -> BatchItemResult:
    """GenerateContentResponse에서 번역 텍스트를 꺼낸다. 사고(thought) 파트는 제외한다."""
    finish_reason = None
    texts: List[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        cand = candidates[0]
        fr = getattr(cand, "finish_reason", None)
        finish_reason = getattr(fr, "name", None) or (str(fr) if fr else None)
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                texts.append(text)

    text = "".join(texts)
    if text.strip():
        return BatchItemResult(key=key, text=text, finish_reason=finish_reason)

    block_reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if block_reason:
        reason = getattr(block_reason, "name", None) or str(block_reason)
        return BatchItemResult(key=key, blocked=True, finish_reason=reason, error=f"프롬프트 차단: {reason}")
    if finish_reason in _BLOCKED_FINISH_REASONS:
        return BatchItemResult(key=key, blocked=True, finish_reason=finish_reason, error=f"응답 차단: {finish_reason}")
    # 내용이 있는 원문에 빈 응답이 오면 실시간 경로와 같이 검열로 본다
    return BatchItemResult(key=key, blocked=True, finish_reason=finish_reason, error="빈 응답")


def parse_inlined_responses(inlined_responses: Optional[List[Any]]) -> List[BatchItemResult]:
    results: List[BatchItemResult] = []
    for item in inlined_responses or []:
        metadata = getattr(item, "metadata", None) or {}
        key = metadata.get("key") if isinstance(metadata, dict) else None
        error = getattr(item, "error", None)
        if error is not None:
            message = getattr(error, "message", None) or str(error)
            code = getattr(error, "code", None)
            results.append(BatchItemResult(key=key, error=f"{code}: {message}" if code else message))
            continue
        response = getattr(item, "response", None)
        if response is None:
            results.append(BatchItemResult(key=key, error="응답 없음"))
            continue
        results.append(_parse_response(key, response))
    return results


def _to_job_info(job: Any) -> BatchJobInfo:
    raw_state = getattr(job, "state", None)
    state = map_job_state(raw_state)
    error = getattr(job, "error", None)
    stats = getattr(job, "completion_stats", None)
    info = BatchJobInfo(
        name=getattr(job, "name", "") or "",
        display_name=getattr(job, "display_name", None),
        state=state,
        raw_state=getattr(raw_state, "name", None) or str(raw_state or ""),
        error_message=(getattr(error, "message", None) or str(error)) if error else None,
        successful_count=getattr(stats, "successful_count", None) if stats else None,
        failed_count=getattr(stats, "failed_count", None) if stats else None,
    )
    if state.is_terminal:
        dest = getattr(job, "dest", None)
        inlined = getattr(dest, "inlined_responses", None) if dest else None
        if inlined is not None:
            info.results = parse_inlined_responses(inlined)
    return info


class GeminiBatchClient:
    """API 키 하나에 고정된 Gemini Batch API 클라이언트."""

    def __init__(self, api_key: str, sdk_client: Optional[Any] = None) -> None:
        if not api_key:
            raise BtgApiClientException("배치 번역에는 Gemini API 키가 필요합니다.")
        self.fingerprint = key_fingerprint(api_key)
        self._client = sdk_client or genai.Client(api_key=api_key)

    def _wrap_error(self, action: str, e: Exception, **context: Any) -> Exception:
        code = getattr(e, "code", None)
        message = getattr(e, "message", None) or str(e)
        text = f"배치 {action} 실패 ({code}): {message}" if code else f"배치 {action} 실패: {message}"
        if action == "제출" and re.search(r"\bfree[ _-]tier\b", str(message), re.IGNORECASE):
            text = f"{text}\n{FREE_TIER_HINT}"
        if code == 429:
            diagnostic = _quota_diagnostics(e)
            diagnostic.update({
                "action": action, "http_status": code, "status": getattr(e, "status", None),
                "key_fingerprint": self.fingerprint, **context,
            })
            logger.error("Gemini 429 진단: %s", json.dumps(diagnostic, ensure_ascii=False))
            reasons = [
                f"{v['type']} (quotaId={v.get('quotaId')}, quotaMetric={v.get('quotaMetric')}, 한도={v.get('quotaValue')})"
                for v in diagnostic["violations"]
            ]
            text += "\n429 원인: " + ("; ".join(reasons) if reasons else "UNKNOWN — 서버가 할당량 식별자를 제공하지 않음")
            return BtgApiRateLimitException(text, original_exception=e)
        if code in (400, 404):
            return BtgApiInvalidRequestException(text, original_exception=e)
        if code in (401, 403):
            return BtgApiClientException(text, original_exception=e)
        return BtgApiClientException(text, original_exception=e)

    async def estimate_input_tokens(self, model: str, requests: List[genai_types.InlinedRequest]) -> Optional[int]:
        async def count() -> int:
            total = 0
            for request in requests:
                contents = list(request.contents or [])
                system = request.config.system_instruction if request.config else None
                if system:
                    # Developer API의 countTokens는 SDK에서 system_instruction 인자를 받지 않는다.
                    # 지시문을 입력 내용으로 함께 세므로 서버 대기열의 실측값이 아닌 추정치다.
                    contents.insert(0, system)
                response = await self._client.aio.models.count_tokens(
                    model=model, contents=contents,
                    config=genai_types.CountTokensConfig(http_options=genai_types.HttpOptions(
                        timeout=10_000, retry_options=genai_types.HttpRetryOptions(attempts=1),
                    )),
                )
                value = response.total_tokens
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError("countTokens 응답에 토큰 수가 없습니다")
                total += value
            return total

        try:
            return await asyncio.wait_for(count(), timeout=60)
        except Exception as e:
            logger.warning("배치 입력 토큰 추정 실패: model=%s error_type=%s code=%s; 추정치=UNKNOWN",
                           model, type(e).__name__, getattr(e, "code", None))
            return None

    async def submit(
        self,
        model: str,
        requests: List[genai_types.InlinedRequest],
        display_name: str,
    ) -> BatchJobInfo:
        estimated_tokens = await self.estimate_input_tokens(model, requests)
        logger.info("배치 제출 예정: model=%s 요청=%d 입력토큰추정=%s (countTokens; 서버 대기열 실측 아님)",
                    model, len(requests), estimated_tokens if estimated_tokens is not None else "UNKNOWN")
        try:
            job = await self._client.aio.batches.create(
                model=model,
                src=requests,
                config=genai_types.CreateBatchJobConfig(display_name=display_name),
            )
        except genai_errors.APIError as e:
            raise self._wrap_error(
                "제출", e, model=model, input_mode="inline", request_count=len(requests),
                estimated_input_tokens=estimated_tokens,
            ) from e
        info = _to_job_info(job)
        info.estimated_input_tokens = estimated_tokens
        logger.info(f"배치 작업 제출: {info.name} ({display_name}, 요청 {len(requests)}개, 상태 {info.raw_state})")
        return info

    async def get(self, name: str) -> BatchJobInfo:
        try:
            job = await self._client.aio.batches.get(name=name)
        except genai_errors.APIError as e:
            raise self._wrap_error("조회", e) from e
        return _to_job_info(job)

    async def cancel(self, name: str) -> None:
        try:
            await self._client.aio.batches.cancel(name=name)
        except genai_errors.APIError as e:
            raise self._wrap_error("취소", e) from e
        logger.info(f"배치 작업 취소 요청: {name}")

    async def find_by_display_name(self, display_name: str, max_jobs: int = 250) -> Optional[BatchJobInfo]:
        """제출 응답을 받기 전에 앱이 종료된 작업을 이름으로 찾는다 (최근 작업부터 `max_jobs`개)."""
        try:
            pager = await self._client.aio.batches.list(config=genai_types.ListBatchJobsConfig(page_size=50))
            scanned = 0
            async for job in pager:
                if getattr(job, "display_name", None) == display_name:
                    return _to_job_info(job)
                scanned += 1
                if scanned >= max_jobs:
                    break
        except genai_errors.APIError as e:
            raise self._wrap_error("목록 조회", e) from e
        return None

    async def upload_file(self, data: bytes, mime_type: str, display_name: str) -> genai_types.File:
        """배치 요청에서 공통으로 참조할 파일(예: PageFold 용어집 PDF)을 한 번 올린다."""
        try:
            uploaded = await self._client.aio.files.upload(
                file=io.BytesIO(data),
                config=genai_types.UploadFileConfig(mime_type=mime_type, display_name=display_name),
            )
        except genai_errors.APIError as e:
            raise self._wrap_error("파일 업로드", e) from e
        logger.info(f"배치용 파일 업로드: {getattr(uploaded, 'name', '?')} ({len(data)} bytes)")
        return uploaded
