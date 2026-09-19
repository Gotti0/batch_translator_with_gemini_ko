# circuit_breaker.py
"""
503 과부하가 이어지면 모든 요청을 잠시 멈추는 전역 서킷브레이커.

503도 무료 티어의 하루 한도(RPD)를 1회 소모한다(로컬 로그 실측). 과부하는 모델 전체의 문제라 키를 바꿔도
소용이 없고, 요청 전에 서버 상태를 미리 알 공식 수단도 없다. 그래서 연속 503을 보면 한동안 요청을 보내지
않는 것으로 쿼터를 지킨다.

스케줄러는 "언제 보낼 수 있나"(간격·동시성)를, 브레이커는 "지금 보내도 되나"(서버 상태)를 맡는다.
"""
import time
from contextlib import contextmanager
from enum import Enum
from typing import Callable, Iterator

try:
    from .error_classifier import ErrorKind, classify
    from .logger_config import setup_logger
except ImportError:
    from infrastructure.error_classifier import ErrorKind, classify
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class BreakerState(Enum):
    CLOSED = "closed"        # 평소
    OPEN = "open"            # 정지 중. 요청을 보내지 않는다
    HALF_OPEN = "half_open"  # 정지가 끝나 요청 1건으로 상태를 확인하는 중


class CircuitBreaker:
    """연속 503 횟수로 열리고, 정지 뒤 첫 요청의 결과로 닫히거나 더 길게 다시 열린다.

    - 닫힘: 503이 아닌 응답(성공, 429, 500 등)이 오면 연속 횟수를 0으로 되돌린다.
    - 열림: 연속 503이 threshold회에 이르면 pause초 동안 요청을 막는다.
    - half-open: 정지가 끝나면 요청을 통과시킨다(스케줄러가 동시 진행 1개라 한 건씩). 503이 아니면 닫고
      정지 시간을 처음 값으로 되돌린다. 다시 503이면 정지 시간을 두 배로(최대 max_pause) 늘려 다시 연다.
    """

    def __init__(
        self,
        threshold: int = 3,
        pause_seconds: float = 300.0,
        max_pause_seconds: float = 1800.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = max(1, int(threshold))
        self.base_pause = pause_seconds
        self.max_pause = max(pause_seconds, max_pause_seconds)
        self._clock = clock
        self.state = BreakerState.CLOSED
        self.consecutive_overloaded = 0
        self._pause = pause_seconds
        self._open_until = 0.0

    def seconds_until_ready(self) -> float:
        """요청을 보내기 전까지 기다려야 할 시간. 정지가 끝났으면 half-open으로 넘어가며 0을 돌려준다."""
        if self.state is not BreakerState.OPEN:
            return 0.0
        remaining = self._open_until - self._clock()
        if remaining > 0:
            return remaining
        self.state = BreakerState.HALF_OPEN
        logger.info("과부하 정지 종료: 요청 1건으로 서버 상태를 확인합니다.")
        return 0.0

    def record_success(self) -> None:
        self._record_not_overloaded()

    def record_error(self, error: BaseException) -> None:
        if classify(error).kind is ErrorKind.OVERLOADED:
            self._record_overloaded()
        else:
            self._record_not_overloaded()

    @contextmanager
    def observe(self) -> Iterator[None]:
        """API 호출 한 번을 감싸 결과를 기록한다. 예외는 그대로 전파한다.

        취소(CancelledError는 BaseException)는 서버 응답이 아니므로 기록하지 않는다.
        """
        try:
            yield
        except Exception as error:
            self.record_error(error)
            raise
        else:
            self.record_success()

    def _record_not_overloaded(self) -> None:
        if self.state is BreakerState.HALF_OPEN:
            logger.info("과부하 해제 확인: 요청을 재개합니다.")
        self.state = BreakerState.CLOSED
        self.consecutive_overloaded = 0
        self._pause = self.base_pause

    def _record_overloaded(self) -> None:
        self.consecutive_overloaded += 1
        if self.state is BreakerState.HALF_OPEN:
            self._pause = min(self._pause * 2, self.max_pause)
            self._open()
        elif self.consecutive_overloaded >= self.threshold:
            self._open()

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self._open_until = self._clock() + self._pause
        logger.warning(
            f"모델 과부하로 {self._pause / 60:.1f}분 정지 (연속 503 {self.consecutive_overloaded}회). "
            f"정지 중에는 요청을 보내지 않아 쿼터를 쓰지 않습니다."
        )
