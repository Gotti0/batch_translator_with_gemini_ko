"""
GeminiClient의 RPM·재시도·키 순환 현재 동작을 고정하는 특성 테스트 (RPM 제어 리팩터 T1).

실제 시간을 쓰지 않고 가상 시계로 요청 시작 시각을 측정한다. 여기 고정한 동작 중 일부는
리팩터 후속 단계에서 의도적으로 바뀐다. 해당 테스트의 docstring에 어느 단계에서 뒤집히는지 적었다.
(설계: docs/brainstorm/2026-09-19-rpm-control-srp.md)
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import infrastructure.gemini_client as gc_module
from infrastructure.gemini_client import (
    GeminiAllApiKeysExhaustedException,
    GeminiClient,
    GeminiContentSafetyException,
)
from test.test_infrastructure.virtual_clock import T0, VirtualClock

KEYS = ["key-AAAAAAAA", "key-BBBBBBBB", "key-CCCCCCCC"]


class _ModuleProxy:
    """모듈 속성은 그대로 위임하고 지정한 속성만 바꿔 끼운다."""

    def __init__(self, target, **overrides):
        self._target = target
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._target, name)


def _server_error(code: int, status: str, message: str) -> genai_errors.APIError:
    body = {"error": {"code": code, "status": status, "message": message}}
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


def err_503():
    return _server_error(503, "UNAVAILABLE", "This model is currently experiencing high demand.")


def err_500():
    return _server_error(500, "INTERNAL", "An internal error has occurred.")


def err_429_quota():
    return _server_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.")


def ok_response(text: str = "ok"):
    return SimpleNamespace(text=text, prompt_feedback=None, candidates=None)


class FakeApi:
    """키별 SDK 클라이언트를 흉내 내고 호출 시각·키를 기록한다.

    script(key, n) 는 그 키의 n번째(0부터) 호출에 대해 예외 인스턴스나 응답을 돌려준다.
    """

    def __init__(self, clock: VirtualClock, script, latency: float = 0.1):
        self.clock = clock
        self.script = script
        self.latency = latency
        self.calls = []  # (key, start, end, kind)
        self._per_key = {}

    def make_client(self, api_key=None, http_options=None, **_):
        sdk = MagicMock()

        async def generate_content(**kwargs):
            n = self._per_key.get(api_key, 0)
            self._per_key[api_key] = n + 1
            start = self.clock.time()
            await self.clock.sleep(self.latency)
            self.calls.append((api_key, start, self.clock.time(), "generate"))
            outcome = self.script(api_key, n)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        async def list_models(**kwargs):
            start = self.clock.time()
            self.calls.append((api_key, start, start, "list"))

            async def _gen():
                yield SimpleNamespace(name="models/gemini-test", display_name="t", description="")

            return _gen()

        sdk.aio.models.generate_content = AsyncMock(side_effect=generate_content)
        sdk.aio.models.list = AsyncMock(side_effect=list_models)
        return sdk

    def starts(self, kind: str = "generate"):
        return [round(s - T0, 6) for _, s, _, k in self.calls if k == kind]

    def keys(self):
        return [k for k, _, _, kind in self.calls if kind == "generate"]


@pytest.fixture
def env():
    """가상 시계를 gemini_client 모듈의 time·asyncio.sleep에 연결하고, 백오프의 난수를 0으로 고정한다."""
    clock = VirtualClock()
    holder = {}

    def build(script, rpm: float = 60.0, latency: float = 0.1, keys=KEYS):
        api = FakeApi(clock, script, latency)
        holder["api"] = api
        with patch.object(gc_module.genai, "Client", side_effect=api.make_client):
            client = GeminiClient(auth_credentials=list(keys), requests_per_minute=rpm)
        return client, api

    patches = [
        patch.object(gc_module, "time", _ModuleProxy(gc_module.time, time=clock.time)),
        patch.object(gc_module, "asyncio", _ModuleProxy(asyncio, sleep=clock.sleep)),
        patch.object(gc_module, "random", _ModuleProxy(gc_module.random, uniform=lambda a, b: 0.0)),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(clock=clock, build=build)
    finally:
        for p in reversed(patches):
            p.stop()


def _gen(client, **kw):
    params = dict(prompt="hello", model_name="gemini-test", max_retries=5, initial_backoff=0.1, max_backoff=1.0)
    params.update(kw)
    return client.generate_text_async(**params)


# ---------------------------------------------------------------------------
# 요청 시작 간격
# ---------------------------------------------------------------------------

def test_concurrent_requests_start_one_interval_apart(env):
    """동시에 들어온 요청도 60/RPM 간격으로 시작한다. (T2 이후에도 유지되어야 하는 동작)"""
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0)

    async def scenario():
        return await asyncio.gather(*[_gen(client) for _ in range(4)])

    results = asyncio.run(env.clock.run(scenario()))

    assert results == ["ok"] * 4
    assert api.starts() == [0.0, 1.0, 2.0, 3.0]


def test_rpm_1_8_interval_is_33_seconds(env):
    """RPM 1.8이면 요청 간격은 60/1.8 ≈ 33.33초다."""
    client, api = env.build(lambda k, n: ok_response(), rpm=1.8)

    async def scenario():
        return await asyncio.gather(_gen(client), _gen(client))

    asyncio.run(env.clock.run(scenario()))

    assert api.starts()[1] == pytest.approx(60.0 / 1.8)


def test_retry_after_503_waits_for_next_rpm_slot(env):
    """503 재시도도 RPM 슬롯을 하나 받는다. 백오프(0.1초)가 슬롯 간격(1초)에 흡수된다."""
    client, api = env.build(lambda k, n: err_503() if n == 0 else ok_response(), rpm=60.0)

    result = asyncio.run(env.clock.run(_gen(client)))

    assert result == "ok"
    assert api.starts() == [0.0, 1.0]


def test_slow_requests_overlap_in_flight(env):
    """현재는 시작 간격만 제어하므로, 응답이 간격보다 느리면 요청이 동시에 진행된다.

    T2(RequestScheduler, in-flight 1)에서 뒤집힌다: 두 번째 요청은 첫 요청이 끝난 뒤 시작해야 한다.
    """
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0, latency=5.0)

    async def scenario():
        return await asyncio.gather(_gen(client), _gen(client))

    asyncio.run(env.clock.run(scenario()))

    (_, s1, e1, _), (_, s2, _, _) = sorted(api.calls, key=lambda c: c[1])
    assert s2 - T0 == 1.0
    assert s2 < e1  # 겹침


def test_list_models_consumes_a_slot(env):
    """list_models도 생성 요청과 같은 RPM 슬롯을 쓴다."""
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0)

    async def scenario():
        await _gen(client)
        return await client.list_models_async()

    models = asyncio.run(env.clock.run(scenario()))

    assert len(models) == 1
    assert api.starts("list") == [1.0]


# ---------------------------------------------------------------------------
# 재시도와 키 순환
# ---------------------------------------------------------------------------

def test_503_retries_on_same_key_then_rotates_through_all_keys(env):
    """503이 계속되면 키마다 (max_retries+1)회씩 시도하고 다음 키로 넘어가 전 키를 소진한다.

    T4·T5b에서 뒤집힌다: 503 재시도는 같은 키로 1회뿐이고, 503으로는 키를 바꾸지 않는다.
    """
    client, api = env.build(lambda k, n: err_503(), rpm=60.0)

    with pytest.raises(GeminiAllApiKeysExhaustedException):
        asyncio.run(env.clock.run(_gen(client, max_retries=2)))

    assert api.keys() == [KEYS[0]] * 3 + [KEYS[1]] * 3 + [KEYS[2]] * 3
    assert api.starts() == [float(i) for i in range(9)]


def test_quota_exhaustion_rotates_to_next_key_immediately(env):
    """할당량 소진(429 RESOURCE_EXHAUSTED)은 재시도 없이 다음 키로 넘어가고 소진 시각을 기록한다."""
    client, api = env.build(lambda k, n: err_429_quota() if k == KEYS[0] else ok_response(), rpm=60.0)

    result = asyncio.run(env.clock.run(_gen(client)))

    assert result == "ok"
    assert api.keys() == [KEYS[0], KEYS[1]]
    assert KEYS[0] in client.key_quota_failure_times
    assert client.current_api_key == KEYS[1]


def test_key_stays_sticky_across_successful_requests(env):
    """성공하는 동안에는 새 요청도 같은 키를 계속 쓴다.

    T4에서 뒤집힌다: 새 요청마다 가장 오래 쉰 키를 고른다.
    """
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0)

    async def scenario():
        for _ in range(3):
            await _gen(client)

    asyncio.run(env.clock.run(scenario()))

    assert api.keys() == [KEYS[0]] * 3


def test_500_exception_is_retried_as_generic_error_not_safety(env):
    """SDK가 500을 예외로 던지면 안전 차단으로 분류되지 않고 일반 오류로 재시도·키 순환된다.

    T5에서 뒤집힌다: 같은 요청에서 500이 연속 2회면 GeminiContentSafetyException이어야 한다.
    """
    client, api = env.build(lambda k, n: err_500(), rpm=60.0, keys=KEYS[:1])

    with pytest.raises(GeminiAllApiKeysExhaustedException) as exc_info:
        asyncio.run(env.clock.run(_gen(client, max_retries=1)))

    assert not isinstance(exc_info.value, GeminiContentSafetyException)
    assert api.keys() == [KEYS[0]] * 2
