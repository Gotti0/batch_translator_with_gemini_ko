"""CircuitBreaker 단위 테스트와, 스케줄러가 열린 브레이커를 기다리는 동작."""
import asyncio
import logging
import os
import sys

import pytest
from google.genai import errors as genai_errors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from infrastructure.circuit_breaker import BreakerState, CircuitBreaker
from infrastructure.request_scheduler import RequestScheduler
from test.test_infrastructure.virtual_clock import T0, VirtualClock


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def err(code, status):
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "status": status, "message": status}})


E503 = err(503, "UNAVAILABLE")
E500 = err(500, "INTERNAL")
E429 = err(429, "RESOURCE_EXHAUSTED")


def make(threshold=3, pause=300.0, max_pause=1800.0):
    clock = FakeClock()
    return CircuitBreaker(threshold, pause, max_pause, clock=clock), clock


def test_opens_after_threshold_consecutive_503():
    b, clock = make()
    b.record_error(E503)
    b.record_error(E503)
    assert b.state is BreakerState.CLOSED
    b.record_error(E503)
    assert b.state is BreakerState.OPEN
    assert b.seconds_until_ready() == 300.0


@pytest.mark.parametrize("breaker_reset", [
    lambda b: b.record_success(),
    lambda b: b.record_error(E500),
    lambda b: b.record_error(E429),
    lambda b: b.record_error(TimeoutError("timed out")),
])
def test_any_non_503_outcome_resets_the_streak(breaker_reset):
    b, _ = make()
    b.record_error(E503)
    b.record_error(E503)
    breaker_reset(b)
    b.record_error(E503)
    b.record_error(E503)
    assert b.state is BreakerState.CLOSED


def test_half_open_after_pause_then_success_closes_and_resets_pause():
    b, clock = make()
    for _ in range(3):
        b.record_error(E503)
    clock.now = 299.9
    assert b.seconds_until_ready() == pytest.approx(0.1)
    clock.now = 300.0
    assert b.seconds_until_ready() == 0.0
    assert b.state is BreakerState.HALF_OPEN
    b.record_success()
    assert b.state is BreakerState.CLOSED
    # 닫힌 뒤 다시 연속 3회면 처음 길이(300초)로 열린다
    for _ in range(3):
        b.record_error(E503)
    assert b.seconds_until_ready() == 300.0


def test_half_open_503_doubles_pause_up_to_max():
    b, clock = make(pause=300.0, max_pause=1000.0)
    for _ in range(3):
        b.record_error(E503)
    pauses = []
    for _ in range(3):
        clock.now += b.seconds_until_ready()
        assert b.seconds_until_ready() == 0.0  # half-open
        b.record_error(E503)
        pauses.append(b.seconds_until_ready())
    assert pauses == [600.0, 1000.0, 1000.0]


def test_observe_records_success_and_errors_and_reraises():
    b, _ = make(threshold=1)
    with b.observe():
        pass
    assert b.state is BreakerState.CLOSED
    with pytest.raises(genai_errors.ServerError):
        with b.observe():
            raise E503
    assert b.state is BreakerState.OPEN


def test_observe_ignores_cancellation():
    b, _ = make(threshold=1)
    with pytest.raises(asyncio.CancelledError):
        with b.observe():
            raise asyncio.CancelledError()
    assert b.state is BreakerState.CLOSED
    assert b.consecutive_overloaded == 0


def test_open_is_logged_with_pause_minutes(caplog):
    b, _ = make()
    with caplog.at_level(logging.WARNING, logger="infrastructure.circuit_breaker"):
        for _ in range(3):
            b.record_error(E503)
    assert any("모델 과부하로 5.0분 정지 (연속 503 3회)" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 스케줄러 연동
# ---------------------------------------------------------------------------

def _scheduler_with_open_breaker(clock):
    breaker = CircuitBreaker(1, 300.0, 1800.0, clock=clock.time)
    breaker.record_error(E503)  # T0에 열림 → T0+300까지 정지
    return RequestScheduler(60.0, clock=clock.time, sleep=clock.sleep, breaker=breaker), breaker


def test_scheduler_waits_until_breaker_pause_ends():
    clock = VirtualClock()
    scheduler, breaker = _scheduler_with_open_breaker(clock)
    started = []

    async def request():
        async with scheduler.slot():
            started.append(clock.time() - T0)

    asyncio.run(clock.run(request()))

    assert started == [300.0]
    assert breaker.state is BreakerState.HALF_OPEN


def test_request_waiting_on_breaker_can_be_cancelled():
    clock = VirtualClock()
    scheduler, _ = _scheduler_with_open_breaker(clock)
    started = []

    async def request(name):
        async with scheduler.slot():
            started.append(name)

    async def scenario():
        doomed = asyncio.ensure_future(request("doomed"))
        await clock.sleep(10.0)
        doomed.cancel()
        await asyncio.sleep(0)
        assert doomed.cancelled()
        await request("next")

    asyncio.run(clock.run(scenario()))

    assert started == ["next"]
