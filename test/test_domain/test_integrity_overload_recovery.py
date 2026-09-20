"""과부하(503)가 난 청크를 성공할 때까지 다시 시도하는지 확인한다.

예전에는 과부하가 난 청크를 실패로 남기고 다음 청크로 넘어갔고, 연속 실패가 한도에 이르면
작업을 멈췄다. 그 결과 실패한 자리는 조립 단계에서 원문으로 남았다. 지금은 같은 청크를
성공할 때까지 다시 시도한다. 과부하가 풀리지 않으면 무한히 머무는데, 멈추는 판단은 사용자의
중단 요청에 맡긴다는 것이 명시적인 결정이다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.translation_service import TranslationService
from infrastructure.gemini_client import GeminiServiceUnavailableException


def _service(side_effect, **config):
    client = MagicMock()
    client.generate_text_async = AsyncMock(side_effect=side_effect)
    base = {
        "model_name": "gemini-test",
        "min_content_safety_chunk_size": 0,
        # 한 줄이 한 청크가 되게 한다.
        "integrity_max_items": 1,
    }
    base.update(config)
    return TranslationService(gemini_client=client, config=base)


def _responder(fail_on, fail_times=None):
    """지정한 원문을 담은 요청에만 과부하를 낸다.

    fail_times가 None이면 계속 실패하고, 숫자면 그 횟수만 실패한 뒤 성공한다.
    """
    calls = []
    failures = {}

    async def respond(**kwargs):
        text = kwargs["prompt"][0].parts[0].text
        calls.append(text)
        for marker in fail_on:
            if marker not in text:
                continue
            seen = failures.get(marker, 0)
            if fail_times is None or seen < fail_times:
                failures[marker] = seen + 1
                raise GeminiServiceUnavailableException(f"모델 과부하(503): {marker}")
        start, end = text.find("["), text.rfind("]") + 1
        import json

        items = json.loads(text[start:end])
        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    return respond, calls


def test_overloaded_chunk_is_retried_until_it_succeeds():
    """과부하가 풀리면 같은 청크가 번역되고 원문이 남지 않는다."""
    respond, calls = _responder(fail_on=["line1"], fail_times=2)
    service = _service(respond)

    result = asyncio.run(service.translate_text_integrity("line0\nline1\nline2"))

    assert result.splitlines() == ["번역:line0", "번역:line1", "번역:line2"]
    # line1을 세 번(실패 2회 + 성공 1회) 불렀다.
    assert sum(1 for c in calls if "line1" in c) == 3


def test_failed_chunk_is_not_skipped():
    """과부하가 난 청크가 성공하기 전에는 다음 청크로 넘어가지 않는다."""
    respond, calls = _responder(fail_on=["line1"], fail_times=2)
    service = _service(respond)

    asyncio.run(service.translate_text_integrity("line0\nline1\nline2"))

    order = ["line1" if "line1" in c else "line2" if "line2" in c else "line0" for c in calls]
    assert order == ["line0", "line1", "line1", "line1", "line2"]


def test_stop_request_breaks_out_of_the_retry_loop():
    """상한이 없으므로 중단 요청이 유일한 탈출구다. 재시도 중에도 먹혀야 한다."""
    respond, calls = _responder(fail_on=["line0"])
    service = _service(respond)

    attempts = {"n": 0}

    def stop_check():
        # 세 번째 시도 직전에 중단을 요청한다.
        attempts["n"] += 1
        return attempts["n"] > 3

    service.set_stop_check_callback(stop_check)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.translate_text_integrity("line0\nline1"))

    # 무한히 돌지 않고 중단 시점에서 멈췄다.
    assert len(calls) < 5
