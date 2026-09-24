"""
Gemini Batch API Client for Neo Batch Translator (BTG)

Gemini Batch API(`client.aio.batches`)와 File API 호출을 한곳에 모은 얇은 래퍼입니다.
SDK의 배치 필드명이 바뀌어도 이 파일만 고치면 되도록, 상위 계층에는 SDK 타입 대신
`BatchJobInfo`·`BatchItemResult`만 돌려줍니다.

배치 작업과 업로드 파일은 제출한 API 키의 프로젝트에만 보이므로, 이 클라이언트는
키 하나에 고정됩니다. 실시간 호출의 키 로테이션은 쓰지 않습니다.
"""

from __future__ import annotations

import hashlib
import io
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

# 무료 티어 키는 Batch API를 쓸 수 없다 (실사용 확인). 제출 거부 시 이유를 알려준다.
FREE_TIER_HINT = "무료 티어 키는 Batch API를 사용할 수 없습니다. 결제가 설정된 유료 키를 '배치용 API 키'에 입력하세요."
_TIER_KEYWORDS = ("free", "tier", "billing", "paid", "quota", "permission")

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


def key_fingerprint(api_key: str) -> str:
    """키를 저장하지 않고 같은 키인지 확인하기 위한 지문."""
    return "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


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

    @staticmethod
    def _wrap_error(action: str, e: Exception) -> Exception:
        code = getattr(e, "code", None)
        message = getattr(e, "message", None) or str(e)
        text = f"배치 {action} 실패 ({code}): {message}" if code else f"배치 {action} 실패: {message}"
        if action == "제출" and (code in (400, 401, 403) or any(k in str(message).lower() for k in _TIER_KEYWORDS)):
            text = f"{text}\n{FREE_TIER_HINT}"
        if code == 429:
            return BtgApiRateLimitException(text, original_exception=e)
        if code in (400, 404):
            return BtgApiInvalidRequestException(text, original_exception=e)
        if code in (401, 403):
            return BtgApiClientException(text, original_exception=e)
        return BtgApiClientException(text, original_exception=e)

    async def submit(
        self,
        model: str,
        requests: List[genai_types.InlinedRequest],
        display_name: str,
    ) -> BatchJobInfo:
        try:
            job = await self._client.aio.batches.create(
                model=model,
                src=requests,
                config=genai_types.CreateBatchJobConfig(display_name=display_name),
            )
        except genai_errors.APIError as e:
            raise self._wrap_error("제출", e) from e
        info = _to_job_info(job)
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
