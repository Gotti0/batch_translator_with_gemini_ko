"""과부하(503)로 청크가 실패해도 남은 청크를 계속 번역하는지 확인한다.

503을 분할 대상에서 빼면서 `_translate_integrity_chunk_with_retry`가 예외를 그대로
올리게 했는데, 무결성 청크 루프에는 그것을 받는 곳이 없어 예외가 작업 전체를 끝냈다.
실행 로그에서 57청크 작업이 3번째 청크에서 종료되는 것으로 드러났다. 의도는 그 청크만
실패로 남기고 이어하기에 맡기는 것이었다.
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


def _responder(fail_on):
    """지정한 원문을 담은 요청에만 과부하를 낸다."""
    calls = []

    async def respond(**kwargs):
        text = kwargs["prompt"][0].parts[0].text
        calls.append(text)
        for marker in fail_on:
            if marker in text:
                raise GeminiServiceUnavailableException(f"모델 과부하(503): {marker}")
        start, end = text.find("["), text.rfind("]") + 1
        import json

        items = json.loads(text[start:end])
        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    return respond, calls


def test_overloaded_chunk_does_not_stop_the_job():
    """가운데 청크가 과부하로 실패해도 뒤 청크가 계속 번역된다."""
    respond, calls = _responder(fail_on=["line1"])
    service = _service(respond)

    result = asyncio.run(service.translate_text_integrity("line0\nline1\nline2"))

    lines = result.splitlines()
    assert lines[0] == "번역:line0"
    assert lines[1] == "line1"  # 실패한 자리는 원문이 남는다
    assert lines[2] == "번역:line2"
    # 세 청크를 모두 시도했다.
    assert len(calls) == 3


def test_consecutive_overloads_stop_the_job():
    """연속 실패가 한도에 이르면 남은 청크를 헛돌지 않고 멈춘다."""
    respond, calls = _responder(fail_on=["line"])
    service = _service(respond, max_consecutive_overloaded_chunks=2)

    with pytest.raises(GeminiServiceUnavailableException):
        asyncio.run(service.translate_text_integrity("line0\nline1\nline2\nline3"))

    # 2개까지만 시도하고 멈춘다.
    assert len(calls) == 2


def test_success_resets_the_consecutive_counter():
    """중간에 성공하면 연속 카운터가 풀려 작업이 이어진다."""
    respond, calls = _responder(fail_on=["line0", "line2"])
    service = _service(respond, max_consecutive_overloaded_chunks=2)

    result = asyncio.run(service.translate_text_integrity("line0\nline1\nline2"))

    lines = result.splitlines()
    assert lines[0] == "line0"
    assert lines[1] == "번역:line1"
    assert lines[2] == "line2"
    assert len(calls) == 3
