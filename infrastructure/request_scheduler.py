# request_scheduler.py
"""
API 요청의 전역 실행 순서를 정하는 스케줄러.

무료 티어 키를 여러 개 돌려 쓰는 사용자가 많아, 여러 키로 동시에 요청을 보내면 악성 사용자로
판정될 위험이 있다. 그래서 요청은 전역에서 한 번에 하나만 진행하고, 요청 시작 사이에는
60/RPM초 이상의 간격을 둔다. 재시도도 새 요청과 똑같이 슬롯을 받는다.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional

try:
    from .logger_config import setup_logger
except ImportError:
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class RequestScheduler:
    """시작 간격(60/RPM)과 동시 진행 1개를 함께 보장한다.

    다음 요청의 시작 시각은 max(이전 요청의 종료 시각, 이전 요청의 시작 시각 + 간격)이다.
    기다리는 요청은 도착 순서대로 슬롯을 받는다.
    """

    def __init__(
        self,
        requests_per_minute: Optional[float],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.requests_per_minute = requests_per_minute
        self.interval = 60.0 / requests_per_minute if requests_per_minute and requests_per_minute > 0 else 0.0
        self.clock = clock  # 같은 시간축을 써야 하는 부품(키 쿨다운 등)이 공유한다
        self._clock = clock
        self._sleep = sleep
        self._in_flight = asyncio.Lock()
        self._last_start: Optional[float] = None

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """요청 하나를 보낼 수 있을 때까지 기다린 뒤, 블록이 끝날 때까지 다른 요청을 막는다.

        대기 중에 취소되면 슬롯을 소비하지 않는다.
        """
        async with self._in_flight:
            await self._wait_for_interval()
            self._last_start = self._clock()
            yield

    async def _wait_for_interval(self) -> None:
        if self._last_start is None or self.interval <= 0:
            return
        wait = self._last_start + self.interval - self._clock()
        if wait <= 0:
            return
        # 1초 이상 기다릴 때만 INFO로 남겨 지연 상황을 쉽게 알아보게 한다 (기존 로그 문구 유지)
        level = logging.INFO if wait >= 1.0 else logging.DEBUG
        scheduled = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + wait))
        logger.log(level, f"RPM({self.requests_per_minute}) 제어: 다음 요청까지 {wait:.3f}초 대기합니다. (예약된 시작: {scheduled})")
        await self._sleep(wait)
