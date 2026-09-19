# error_classifier.py
"""
Gemini API 예외를 재시도 판단에 쓰는 종류로 분류한다.

분류만 하고 행동은 정하지 않는다. 행동(재시도, 키 전환, 중단)은 RetryPolicy가 정한다.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Optional


class ErrorKind(Enum):
    INVALID_REQUEST = "invalid_request"  # 400·401·403·404 계열. 잘못된 키이거나 요청 자체의 문제
    QUOTA_DAILY = "quota_daily"          # 하루 한도(RPD, 일일 토큰) 소진
    QUOTA_MINUTE = "quota_minute"        # 분당 한도(RPM, 분당 토큰) 소진
    QUOTA_UNKNOWN = "quota_unknown"      # 할당량 소진이지만 기간을 알 수 없음
    SERVER_500 = "server_500"            # 500 INTERNAL. Gemini는 검열 응답을 500으로 보내기도 한다
    OVERLOADED = "overloaded"            # 503. 모델 전체의 과부하
    TRANSIENT = "transient"              # 그 밖의 일시 오류(timeout, 쿼터 아닌 429 등)


@dataclass(frozen=True)
class ClassifiedError:
    kind: ErrorKind
    retry_delay: Optional[float] = None  # 서버가 알려준 재시도 대기(초). 분당 한도 쿨다운에 쓴다
    model: Optional[str] = None          # 할당량이 걸린 모델 (quotaDimensions.model)


_INVALID_REQUEST_PATTERNS = [
    "Invalid API key", "API key not valid", "Permission denied",
    "Invalid model name", "model is not found", "400 Bad Request",
    "Invalid JSON payload", "Could not find model",
    "Publisher Model .* not found", "invalid_scope", "INVALID_ARGUMENT",
    "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND",
]
_QUOTA_PATTERNS = [
    "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED", "Quota exceeded", "quota.*exceeded",
    "Resource has been exhausted", "resource.*exhausted",
]
_RATE_LIMIT_PATTERNS = ["rateLimitExceeded", "429", "Too Many Requests"]
_OVERLOADED_PATTERNS = ["503", "UNAVAILABLE", "Service Unavailable", "The model is overloaded", "high demand"]
_SERVER_500_PATTERNS = [r"\b500\b", r"\bINTERNAL\b"]


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _status_code(error: Any) -> Optional[int]:
    # google.genai APIError.code는 int, google.api_core 예외의 code는 HTTPStatus(IntEnum)
    value = getattr(error, "code", None)
    return int(value) if isinstance(value, int) else None


def _error_details(error: Any) -> List[dict]:
    """APIError.details(응답 JSON)에서 error.details 목록을 꺼낸다."""
    body = getattr(error, "details", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        details = inner.get("details") if isinstance(inner, dict) else None
        if isinstance(details, list):
            return [d for d in details if isinstance(d, dict)]
    return []


def _parse_duration(value: Any) -> Optional[float]:
    if isinstance(value, str):
        m = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)s\s*", value)
        if m:
            return float(m.group(1))
    return None


def _quota_info(error: Any, text: str):
    """(기간 종류, retry_delay, model). 기간은 'daily' | 'minute' | None."""
    quota_ids: List[str] = []
    model = None
    retry_delay = None
    for detail in _error_details(error):
        kind = str(detail.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in detail.get("violations", []) or []:
                if not isinstance(violation, dict):
                    continue
                quota_ids.append(str(violation.get("quotaId", "")))
                dims = violation.get("quotaDimensions") or {}
                if isinstance(dims, dict) and dims.get("model") and model is None:
                    model = dims["model"]
        elif kind.endswith("RetryInfo"):
            retry_delay = _parse_duration(detail.get("retryDelay"))

    haystack = " ".join(quota_ids) if quota_ids else text
    # 여러 위반이 함께 오면 하루 한도를 우선한다. 분당 한도만 풀려도 하루 한도는 그대로이기 때문이다
    if re.search(r"PerDay", haystack):
        period = "daily"
    elif re.search(r"PerMinute", haystack):
        period = "minute"
    else:
        period = None
    return period, retry_delay, model


def classify(error: BaseException) -> ClassifiedError:
    text = str(error)
    code = _status_code(error)

    if code in (400, 401, 403, 404) or _matches(text, _INVALID_REQUEST_PATTERNS):
        # 429 본문에 NOT_FOUND 같은 단어가 섞일 수 있으므로 할당량 판정을 먼저 본다
        if not (code == 429 or _matches(text, _QUOTA_PATTERNS)):
            return ClassifiedError(ErrorKind.INVALID_REQUEST)

    if code == 429 or _matches(text, _QUOTA_PATTERNS) or _matches(text, _RATE_LIMIT_PATTERNS):
        if _matches(text, _QUOTA_PATTERNS):
            period, retry_delay, model = _quota_info(error, text)
            kind = {"daily": ErrorKind.QUOTA_DAILY, "minute": ErrorKind.QUOTA_MINUTE}.get(period, ErrorKind.QUOTA_UNKNOWN)
            return ClassifiedError(kind, retry_delay=retry_delay, model=model)
        return ClassifiedError(ErrorKind.TRANSIENT)

    if code == 503 or _matches(text, _OVERLOADED_PATTERNS):
        return ClassifiedError(ErrorKind.OVERLOADED)

    if code == 500 or _matches(text, _SERVER_500_PATTERNS):
        return ClassifiedError(ErrorKind.SERVER_500)

    return ClassifiedError(ErrorKind.TRANSIENT)
