"""RequestScheduler 단위 테스트: 시작 간격, 동시 진행 1개, 도착 순서, 취소, 로그."""
import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from infrastructure.request_scheduler import RequestScheduler
from test.test_infrastructure.virtual_clock import T0, VirtualClock


@pytest.fixture
def clock():
    return VirtualClock()


def make(clock, rpm):
    return RequestScheduler(rpm, clock=clock.time, sleep=clock.sleep)


class Recorder:
    """슬롯 안에서 지정한 시간만큼 머무르며 (이름, 시작, 종료)를 기록한다."""

    def __init__(self, clock, scheduler):
        self.clock = clock
        self.scheduler = scheduler
        self.log = []

    async def request(self, name, duration=0.1):
        async with self.scheduler.slot():
            start = self.clock.time()
            await self.clock.sleep(duration)
            self.log.append((name, round(start - T0, 6), round(self.clock.time() - T0, 6)))

    def starts(self):
        return [s for _, s, _ in self.log]

    def names(self):
        return [n for n, _, _ in self.log]


def run(clock, make_awaitable):
    """이벤트 루프 안에서 awaitable을 만들어 가상 시계로 끝까지 돌린다."""

    async def main():
        return await make_awaitable()

    return asyncio.run(clock.run(main()))


def test_fast_requests_start_one_interval_apart(clock):
    rec = Recorder(clock, make(clock, 60.0))

    run(clock, lambda: asyncio.gather(*[rec.request(i) for i in range(4)]))

    assert rec.starts() == [0.0, 1.0, 2.0, 3.0]


def test_rpm_1_8_interval_is_33_seconds(clock):
    rec = Recorder(clock, make(clock, 1.8))

    run(clock, lambda: asyncio.gather(rec.request("a"), rec.request("b")))

    assert rec.starts()[1] == pytest.approx(60.0 / 1.8)


def test_slow_requests_never_overlap(clock):
    """응답이 간격보다 느리면 다음 요청은 앞 요청이 끝난 뒤에 시작한다. 여러 키 동시 호출을 막는 핵심 보장."""
    rec = Recorder(clock, make(clock, 60.0))

    run(clock, lambda: asyncio.gather(*[rec.request(i, duration=5.0) for i in range(3)]))

    assert rec.log == [(0, 0.0, 5.0), (1, 5.0, 10.0), (2, 10.0, 15.0)]


def test_next_start_is_max_of_previous_end_and_interval(clock):
    """시작 시각 = max(앞 요청 종료, 앞 요청 시작 + 간격)."""
    rec = Recorder(clock, make(clock, 60.0))

    async def scenario():
        await asyncio.gather(rec.request("slow", 3.0), rec.request("fast", 0.1), rec.request("last", 0.1))

    run(clock, scenario)

    assert rec.starts() == [0.0, 3.0, 4.0]


def test_idle_gap_longer_than_interval_does_not_wait(clock):
    rec = Recorder(clock, make(clock, 60.0))

    async def scenario():
        await rec.request("a")
        await clock.sleep(10.0)
        await rec.request("b")

    run(clock, scenario)

    assert rec.starts() == [0.0, 10.1]


@pytest.mark.parametrize("rpm", [None, 0, -1])
def test_no_rpm_limit_still_serializes_requests(clock, rpm):
    """RPM 제한이 없어도 동시 진행 1개는 지킨다."""
    scheduler = make(clock, rpm)
    rec = Recorder(clock, scheduler)

    run(clock, lambda: asyncio.gather(*[rec.request(i, duration=2.0) for i in range(3)]))

    assert scheduler.interval == 0.0
    assert rec.starts() == [0.0, 2.0, 4.0]


def test_waiters_get_slots_in_arrival_order(clock):
    rec = Recorder(clock, make(clock, 60.0))

    run(clock, lambda: asyncio.gather(*[rec.request(name) for name in "abcde"]))

    assert rec.names() == list("abcde")


def test_cancel_while_queued_does_not_consume_a_slot(clock):
    rec = Recorder(clock, make(clock, 60.0))

    async def scenario():
        first = asyncio.ensure_future(rec.request("first", 0.1))
        doomed = asyncio.ensure_future(rec.request("doomed", 0.1))
        third = asyncio.ensure_future(rec.request("third", 0.1))
        await asyncio.sleep(0)
        doomed.cancel()
        await asyncio.gather(first, third)
        assert doomed.cancelled()

    run(clock, scenario)

    assert rec.log == [("first", 0.0, 0.1), ("third", 1.0, 1.1)]


def test_cancel_during_interval_wait_releases_slot_and_keeps_schedule(clock):
    """간격을 기다리던 요청이 취소되면 다음 요청이 원래 예정 시각에 시작한다."""
    rec = Recorder(clock, make(clock, 60.0))

    async def scenario():
        await rec.request("first", 0.1)
        doomed = asyncio.ensure_future(rec.request("doomed", 0.1))
        await clock.sleep(0.5)  # doomed는 1.0까지 간격 대기 중
        doomed.cancel()
        await rec.request("next", 0.1)
        assert doomed.cancelled()

    run(clock, scenario)

    assert rec.log == [("first", 0.0, 0.1), ("next", 1.0, 1.1)]


def test_exception_inside_slot_releases_it(clock):
    scheduler = make(clock, 60.0)
    rec = Recorder(clock, scheduler)

    async def failing():
        async with scheduler.slot():
            raise RuntimeError("boom")

    async def scenario():
        with pytest.raises(RuntimeError):
            await failing()
        await rec.request("after")

    run(clock, scenario)

    assert rec.starts() == [1.0]


def test_long_wait_is_logged_at_info_with_existing_wording(clock, caplog):
    rec = Recorder(clock, make(clock, 1.8))

    with caplog.at_level(logging.DEBUG, logger="infrastructure.request_scheduler"):
        run(clock, lambda: asyncio.gather(rec.request("a"), rec.request("b")))

    waits = [r for r in caplog.records if "RPM(1.8) 제어: 다음 요청까지" in r.getMessage()]
    assert len(waits) == 1
    assert waits[0].levelno == logging.INFO
    assert "33.233초" in waits[0].getMessage()  # 첫 요청이 끝난 0.1초 시점부터 33.333초까지


def test_short_wait_is_logged_at_debug(clock, caplog):
    rec = Recorder(clock, make(clock, 120.0))  # 간격 0.5초

    with caplog.at_level(logging.DEBUG, logger="infrastructure.request_scheduler"):
        run(clock, lambda: asyncio.gather(rec.request("a", 0.1), rec.request("b", 0.1)))

    waits = [r for r in caplog.records if "제어: 다음 요청까지" in r.getMessage()]
    assert [r.levelno for r in waits] == [logging.DEBUG]
