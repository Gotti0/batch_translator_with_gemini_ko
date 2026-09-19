# retry_policy.py
"""
한 요청의 재시도 상태를 들고, 분류된 오류마다 다음 행동을 정한다.

- 503 과부하: 같은 키로 요청당 1회만 재시도. 503도 하루 한도를 쓰므로 반복하지 않는다(과부하가 이어지면
  서킷브레이커가 요청을 멈춘다)
- 그 밖의 일시 오류(timeout 등): 같은 키로 요청당 max_retries회까지 백오프 후 재시도
- 500: 같은 요청에서 처음이면 재시도, 연속 두 번째면 검열로 판정한다. Gemini는 검열 응답을 500으로
  보내기도 하고, 오류 본문으로는 진짜 서버 오류와 구별되지 않는다(사용자 실측). 같은 청크에서 반복되는지만이
  남은 단서다.
- 할당량 소진: 키 전환. 하루 한도는 태평양 시간 자정(리셋)까지, 분당 한도는 서버가 알려준 대기만큼 쿨다운
- 요청 오류: 키 전환(잘못된 키일 수 있음). 쿨다운 없음
"""
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

try:
    from .error_classifier import ClassifiedError, ErrorKind
except ImportError:
    from infrastructure.error_classifier import ClassifiedError, ErrorKind

try:
    from zoneinfo import ZoneInfo
    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # Windows에 tzdata가 없으면 ZoneInfo가 실패한다
    _PACIFIC = None

# tzdata가 없을 때는 PST(UTC-8)로 계산한다. 서머타임 기간에는 실제 리셋보다 1시간 늦게 풀리지만
# 일찍 풀려 헛요청을 보내지는 않는다.
_PACIFIC_FALLBACK = timezone(timedelta(hours=-8))
# 리셋 직후 서버 시계 차이로 다시 429를 맞지 않도록 둔 여유
_DAILY_RESET_MARGIN_SECONDS = 60.0


def seconds_until_daily_reset(now_epoch: float) -> float:
    """무료 티어 하루 한도가 리셋되는 태평양 시간 자정까지 남은 초(여유 포함)."""
    tz = _PACIFIC or _PACIFIC_FALLBACK
    now = datetime.fromtimestamp(now_epoch, tz)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # 같은 tzinfo끼리 빼면 서머타임 전환을 무시하므로 UTC로 바꿔서 뺀다
    remaining = next_midnight.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    return remaining.total_seconds() + _DAILY_RESET_MARGIN_SECONDS


class Action(Enum):
    RETRY_SAME_KEY = "retry_same_key"
    SWITCH_KEY = "switch_key"
    FAIL_SAFETY = "fail_safety"
    FAIL_RETRIES_EXHAUSTED = "fail_retries_exhausted"
    FAIL_OVERLOADED = "fail_overloaded"  # 503 재시도까지 실패. 이 요청만 실패로 끝내고 작업은 계속한다


@dataclass(frozen=True)
class Decision:
    action: Action
    delay: float = 0.0                       # RETRY_SAME_KEY 전에 기다릴 시간
    cooldown_seconds: Optional[float] = None  # SWITCH_KEY 때 현재 키에 걸 쿨다운. None이면 쿨다운 없음
    cooldown_model: Optional[str] = None      # 쿨다운을 특정 모델에만 걸 때


class RetryPolicy:
    MINUTE_QUOTA_DEFAULT_COOLDOWN = 60.0
    UNKNOWN_QUOTA_COOLDOWN = 100.0
    OVERLOADED_RETRY_LIMIT = 1

    def __init__(
        self,
        max_retries: int,
        initial_backoff: float,
        max_backoff: float,
        *,
        jitter: Callable[[], float] = lambda: random.uniform(0, 1),
        wall_clock: Callable[[], float] = time.time,
    ):
        self.max_retries = max_retries
        self.attempt = 0  # 지금까지 한 재시도 횟수
        self._backoff = initial_backoff
        self._max_backoff = max_backoff
        self._jitter = jitter
        self._wall_clock = wall_clock
        self._last_was_500 = False
        self._overloaded_retries = 0

    def on_error(self, error: ClassifiedError) -> Decision:
        was_500, self._last_was_500 = self._last_was_500, error.kind is ErrorKind.SERVER_500

        if error.kind is ErrorKind.INVALID_REQUEST:
            return Decision(Action.SWITCH_KEY)
        if error.kind is ErrorKind.QUOTA_DAILY:
            return Decision(Action.SWITCH_KEY, cooldown_seconds=seconds_until_daily_reset(self._wall_clock()),
                            cooldown_model=error.model)
        if error.kind is ErrorKind.QUOTA_MINUTE:
            return Decision(Action.SWITCH_KEY, cooldown_seconds=error.retry_delay or self.MINUTE_QUOTA_DEFAULT_COOLDOWN,
                            cooldown_model=error.model)
        if error.kind is ErrorKind.QUOTA_UNKNOWN:
            return Decision(Action.SWITCH_KEY, cooldown_seconds=self.UNKNOWN_QUOTA_COOLDOWN)
        if error.kind is ErrorKind.SERVER_500 and was_500:
            return Decision(Action.FAIL_SAFETY)
        if error.kind is ErrorKind.OVERLOADED:
            if self._overloaded_retries >= self.OVERLOADED_RETRY_LIMIT or self.attempt >= self.max_retries:
                return Decision(Action.FAIL_OVERLOADED)
            self._overloaded_retries += 1
        return self._retry_or_give_up()

    def _retry_or_give_up(self) -> Decision:
        if self.attempt >= self.max_retries:
            return Decision(Action.FAIL_RETRIES_EXHAUSTED)
        delay = self._backoff + self._jitter()
        self.attempt += 1
        self._backoff = min(self._backoff * 2, self._max_backoff)
        return Decision(Action.RETRY_SAME_KEY, delay=delay)
