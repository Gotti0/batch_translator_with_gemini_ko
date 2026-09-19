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
    GeminiRetriesExhaustedException,
)
from infrastructure.request_scheduler import RequestScheduler
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


def err_400_bad_key():
    return _server_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")


def err_429_quota_with(quota_id, retry_delay="20s", model="gemini-test"):
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                      "details": [
                          {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                           "violations": [{"quotaId": quota_id, "quotaDimensions": {"model": model}}]},
                          {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
                      ]}}
    return genai_errors.ClientError(429, body)


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
    """가상 시계를 스케줄러와 gemini_client 모듈의 time·asyncio.sleep(백오프)에 연결하고, 백오프의 난수를 0으로 고정한다."""
    clock = VirtualClock()
    holder = {}

    def build(script, rpm: float = 60.0, latency: float = 0.1, keys=KEYS):
        api = FakeApi(clock, script, latency)
        holder["api"] = api
        with patch.object(gc_module.genai, "Client", side_effect=api.make_client):
            scheduler = RequestScheduler(rpm, clock=clock.time, sleep=clock.sleep)
            client = GeminiClient(auth_credentials=list(keys), requests_per_minute=rpm, scheduler=scheduler)
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


def test_slow_requests_never_overlap_in_flight(env):
    """응답이 간격보다 느려도 두 번째 요청은 첫 요청이 끝난 뒤에 시작한다.

    T3에서 뒤집혔다: 이전에는 시작 간격만 제어해 느린 요청이 동시에 진행됐다(시작 1.0초, 겹침).
    """
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0, latency=5.0)

    async def scenario():
        return await asyncio.gather(_gen(client), _gen(client))

    asyncio.run(env.clock.run(scenario()))

    (_, s1, e1, _), (_, s2, _, _) = sorted(api.calls, key=lambda c: c[1])
    assert s2 == e1  # 앞 요청 종료(5.0초) 직후 시작, 겹침 없음


def test_backoff_happens_outside_the_slot(env):
    """실패한 요청이 백오프로 기다리는 동안에는 슬롯을 놓아, 다른 요청이 먼저 나간다."""
    client, api = env.build(lambda k, n: err_503() if (k, n) == (KEYS[0], 0) else ok_response(), rpm=60.0)

    async def scenario():
        failing = _gen(client, initial_backoff=5.0)  # 0.1초에 503, 5.1초까지 백오프
        other = _gen(client)
        return await asyncio.gather(failing, other)

    asyncio.run(env.clock.run(scenario()))

    # 첫 요청 0.0 → 두 번째 요청이 백오프 중에 1.0 → 첫 요청의 재시도가 5.1
    assert api.starts() == [0.0, 1.0, 5.1]


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
# 재시도와 키 선택 (T4: 새 요청은 가장 오래 쉰 키, 재시도는 같은 키, 소진 시에만 전환)
# ---------------------------------------------------------------------------

def test_new_requests_use_least_recently_used_key(env):
    """새 요청마다 가장 오래 쉰 키를 고른다.

    T4에서 뒤집혔다: 이전에는 성공하는 동안 같은 키를 계속 썼다(sticky).
    """
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0)

    async def scenario():
        for _ in range(4):
            await _gen(client)

    asyncio.run(env.clock.run(scenario()))

    assert api.keys() == [KEYS[0], KEYS[1], KEYS[2], KEYS[0]]


def test_queued_requests_pick_keys_when_sent(env):
    """동시에 대기하던 요청도 보내는 순간에 키를 골라 고르게 나뉜다."""
    client, api = env.build(lambda k, n: ok_response(), rpm=60.0)

    async def scenario():
        return await asyncio.gather(*[_gen(client) for _ in range(4)])

    asyncio.run(env.clock.run(scenario()))

    assert api.keys() == [KEYS[0], KEYS[1], KEYS[2], KEYS[0]]


def test_transient_errors_retry_on_same_key_then_fail(env):
    """503이 계속되면 같은 키로 (max_retries+1)회만 시도하고 GeminiRetriesExhaustedException으로 끝난다.

    T4에서 뒤집혔다: 이전에는 키마다 (max_retries+1)회씩 모든 키를 돌았다(키 3개면 9회).
    예외는 GeminiAllApiKeysExhaustedException을 상속해 호출부의 작업 중단 동작은 유지된다.
    T5b에서 503 재시도는 1회로 줄어든다.
    """
    client, api = env.build(lambda k, n: err_503(), rpm=60.0)

    with pytest.raises(GeminiRetriesExhaustedException) as exc_info:
        asyncio.run(env.clock.run(_gen(client, max_retries=2)))

    assert isinstance(exc_info.value, GeminiAllApiKeysExhaustedException)
    assert api.keys() == [KEYS[0]] * 3
    assert api.starts() == [0.0, 1.0, 2.0]


def test_retry_keeps_key_even_after_other_requests(env):
    """다른 요청이 사이에 끼어도 재시도는 처음 고른 키로 보낸다."""
    client, api = env.build(lambda k, n: err_503() if (k, n) == (KEYS[0], 0) else ok_response(), rpm=60.0)

    async def scenario():
        failing = _gen(client, initial_backoff=5.0)
        other = _gen(client)
        return await asyncio.gather(failing, other)

    asyncio.run(env.clock.run(scenario()))

    # 첫 요청 A(503) → 다른 요청은 B → A의 재시도는 다시 A
    assert api.keys() == [KEYS[0], KEYS[1], KEYS[0]]


def test_quota_exhaustion_switches_key_and_cools_down(env):
    """할당량 소진(429 RESOURCE_EXHAUSTED)은 재시도 없이 다른 키로 넘어가고, 소진 키는 쿨다운에 들어간다."""
    client, api = env.build(lambda k, n: err_429_quota() if k == KEYS[0] else ok_response(), rpm=60.0)

    result = asyncio.run(env.clock.run(_gen(client)))

    assert result == "ok"
    assert api.keys() == [KEYS[0], KEYS[1]]
    assert client._key_pool.is_cooling_down(KEYS[0])
    assert client.current_api_key == KEYS[1]


def test_quota_switch_does_not_consume_retry_budget(env):
    """키 전환 뒤에도 일시 오류 재시도 예산은 그대로다."""
    def script(k, n):
        if k == KEYS[0]:
            return err_429_quota()
        return err_503() if n == 0 else ok_response()

    client, api = env.build(script, rpm=60.0)

    result = asyncio.run(env.clock.run(_gen(client, max_retries=1)))

    assert result == "ok"
    assert api.keys() == [KEYS[0], KEYS[1], KEYS[1]]


def test_cooling_down_key_is_skipped_by_new_requests(env):
    client, api = env.build(lambda k, n: err_429_quota() if k == KEYS[0] else ok_response(), rpm=60.0)

    async def scenario():
        for _ in range(3):
            await _gen(client)

    asyncio.run(env.clock.run(scenario()))

    # 1: A 소진 → B, 2: C, 3: A는 쿨다운이라 B
    assert api.keys() == [KEYS[0], KEYS[1], KEYS[2], KEYS[1]]


def test_all_keys_exhausted_raises(env):
    client, api = env.build(lambda k, n: err_429_quota(), rpm=60.0)

    with pytest.raises(GeminiAllApiKeysExhaustedException) as exc_info:
        asyncio.run(env.clock.run(_gen(client)))

    assert not isinstance(exc_info.value, GeminiRetriesExhaustedException)
    assert api.keys() == KEYS


def test_invalid_key_error_moves_to_unused_key_without_cooldown(env):
    """잘못된 키 같은 요청 오류는 이 요청에서 아직 안 쓴 키로 넘어가되, 쿨다운에 넣지 않는다 (T5에서 재분류)."""
    client, api = env.build(lambda k, n: err_400_bad_key() if k == KEYS[0] else ok_response(), rpm=60.0)

    result = asyncio.run(env.clock.run(_gen(client)))

    assert result == "ok"
    assert api.keys() == [KEYS[0], KEYS[1]]
    assert not client._key_pool.is_cooling_down(KEYS[0])


def test_consecutive_500_is_treated_as_content_safety(env):
    """같은 요청에서 500이 연속 2회면 검열로 판정해 GeminiContentSafetyException을 던진다(상위의 청크 분할로 이어짐).

    T5에서 뒤집혔다: 이전에는 500 예외가 일반 오류로 재시도되다 전 키 소진으로 끝나 청크 분할이 일어나지 않았다.
    """
    client, api = env.build(lambda k, n: err_500(), rpm=60.0, keys=KEYS[:1])

    with pytest.raises(GeminiContentSafetyException):
        asyncio.run(env.clock.run(_gen(client, max_retries=5)))

    assert api.keys() == [KEYS[0]] * 2


def test_single_500_then_success_is_not_safety(env):
    client, api = env.build(lambda k, n: err_500() if n == 0 else ok_response(), rpm=60.0)

    assert asyncio.run(env.clock.run(_gen(client))) == "ok"
    assert api.keys() == [KEYS[0]] * 2


def test_500_then_503_then_500_is_not_consecutive(env):
    outcomes = [err_500(), err_503(), err_500(), ok_response()]
    client, api = env.build(lambda k, n: outcomes[n], rpm=60.0, keys=KEYS[:1])

    assert asyncio.run(env.clock.run(_gen(client))) == "ok"
    assert len(api.keys()) == 4


def test_daily_quota_cools_key_until_pacific_midnight_for_that_model(env):
    """하루 한도 소진 키는 태평양 시간 자정까지 그 모델에 쓰이지 않는다. retryDelay(20초)는 무시한다.

    가상 시계 T0(1970-01-12 13:46:40 UTC)는 PST 05:46:40이라 자정까지 18시간 13분 20초(+여유 60초).
    """
    daily = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    client, api = env.build(lambda k, n: err_429_quota_with(daily) if (k, n) == (KEYS[0], 0) else ok_response(),
                            rpm=60.0)
    pool = client._key_pool

    asyncio.run(env.clock.run(_gen(client)))

    assert api.keys() == [KEYS[0], KEYS[1]]
    assert pool.is_cooling_down(KEYS[0], "gemini-test")
    assert not pool.is_cooling_down(KEYS[0], "other-model")  # 다른 모델에는 쓸 수 있다
    reset_at = T0 + 18 * 3600 + 13 * 60 + 20 + 60  # PST 자정 + 여유 60초
    env.clock.now = reset_at - 1
    assert pool.is_cooling_down(KEYS[0], "gemini-test")
    env.clock.now = reset_at
    assert not pool.is_cooling_down(KEYS[0], "gemini-test")


def test_minute_quota_cools_key_for_server_retry_delay(env):
    minute = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    client, api = env.build(lambda k, n: err_429_quota_with(minute, "7s") if (k, n) == (KEYS[0], 0) else ok_response(),
                            rpm=60.0)
    pool = client._key_pool

    asyncio.run(env.clock.run(_gen(client)))

    assert api.keys() == [KEYS[0], KEYS[1]]
    assert pool.is_cooling_down(KEYS[0], "gemini-test")
    env.clock.now += 7
    assert not pool.is_cooling_down(KEYS[0], "gemini-test")


def test_consecutive_500_leads_to_chunk_splitting_in_translation_service(env):
    """연속 500 → GeminiContentSafetyException → TranslationService의 청크 분할 재번역까지 이어진다."""
    from domain.translation_service import TranslationService

    client, api = env.build(lambda k, n: err_500() if n < 2 else ok_response("번역"), rpm=60.0, keys=KEYS[:1])
    service = TranslationService(client, {"model_name": "gemini-test", "chunk_size": 1000})
    text = "\n".join(f"문장 {i}입니다. 충분히 긴 줄을 만들기 위해 내용을 덧붙입니다." for i in range(20))

    result = asyncio.run(env.clock.run(service.translate_text_with_content_safety_retry_async(text, min_chunk_size=50)))

    assert "번역" in result
    assert "실패" not in result and "오류" not in result
    assert len(api.keys()) > 3  # 원 청크 2회(500) + 분할된 서브 청크들
