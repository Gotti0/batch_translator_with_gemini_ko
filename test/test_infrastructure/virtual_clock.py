"""속도 제어 테스트용 가상 시계. 실제로 기다리지 않고 요청 시각을 결정적으로 잰다."""
import asyncio
import heapq

_real_sleep = asyncio.sleep

T0 = 1_000_000.0


class VirtualClock:
    """모든 태스크가 대기 상태가 되면 가장 이른 대기 시각으로 시간을 건너뛰는 가상 시계."""

    def __init__(self, start: float = T0):
        self.now = start
        self._heap = []
        self._seq = 0

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._heap, (self.now + max(0.0, delay), self._seq, fut))
        self._seq += 1
        await fut

    async def run(self, coro):
        task = asyncio.ensure_future(coro)
        while not task.done():
            for _ in range(50):
                await _real_sleep(0)
            if task.done():
                break
            while self._heap and self._heap[0][2].done():
                heapq.heappop(self._heap)
            if not self._heap:
                raise RuntimeError("교착: 진행할 태스크도 예약된 대기도 없음")
            wake_at, _, fut = heapq.heappop(self._heap)
            self.now = max(self.now, wake_at)
            fut.set_result(None)
        return task.result()
