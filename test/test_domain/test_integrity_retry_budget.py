"""무결성 청크 재시도의 두 예산(분할 깊이, 누락 재시도 횟수)이 독립인지 확인한다.

두 재귀 경로가 depth 인자 하나를 공유하던 시절에는 누락 재시도가 검열 분할의 깊이를
잠식하고, 깊이 내려간 조각에서는 누락 재시도가 막혔다. 여기서는 그 분리를 고정한다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dtos import TranslationUnit
from domain.translation_service import TranslationService
from infrastructure.gemini_client import (
    GeminiContentSafetyException,
    GeminiServiceUnavailableException,
    GeminiAllApiKeysExhaustedException,
)


def _units(count):
    return [TranslationUnit(id=str(i), text=f"line{i}") for i in range(count)]


def _service(side_effect, **config):
    client = MagicMock()
    client.generate_text_async = AsyncMock(side_effect=side_effect)
    base = {"model_name": "gemini-test"}
    base.update(config)
    return TranslationService(gemini_client=client, config=base)


def _requested_id(kwargs):
    """목 호출에 실린 프롬프트에서 첫 번째 항목 id를 꺼낸다."""
    text = kwargs["prompt"][0].parts[0].text
    return text.split('"id": "')[1].split('"')[0]


def test_targeted_retry_does_not_consume_split_budget():
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [{"id": "0", "translated_text": "t0"}, {"id": "1", "translated_text": "t1"}]
        if len(calls) == 2:
            # 누락분(2, 3) 재요청이 검열당한다. 분할 예산이 남아 있어야 복구된다.
            raise GeminiContentSafetyException("censored")
        return [{"id": _requested_id(kwargs), "translated_text": "split-ok"}]

    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(4)))

    assert len(calls) == 4  # 최초 + 누락 재시도 + 분할된 두 조각
    assert result["2"] == "split-ok"
    assert result["3"] == "split-ok"


def test_targeted_retry_stops_at_its_own_limit_and_keeps_source():
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return [{"id": "0", "translated_text": "t0"}]  # 항상 일부만 돌려준다

    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(3)))

    assert len(calls) == 2  # 최초 + 누락 재시도 1회(기본 한도)
    assert result["1"] == "line1"  # 한도 도달분은 빈 문자열이 아니라 원문으로 남는다
    assert result["2"] == "line2"


def test_censorship_split_stops_at_depth_limit():
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        raise GeminiContentSafetyException("censored")

    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(8)))

    assert len(calls) == 7  # 1 + 2 + 4, 깊이 2에서 멈춘다
    assert all(result[str(i)] == f"line{i}" for i in range(8))


def test_split_disabled_when_content_safety_retry_is_off():
    async def respond(**kwargs):
        raise GeminiContentSafetyException("censored")

    service = _service(respond, use_content_safety_retry=False)
    with pytest.raises(GeminiContentSafetyException):
        asyncio.run(service._translate_integrity_chunk_with_retry(_units(4)))


@pytest.mark.parametrize(
    "error",
    [
        GeminiServiceUnavailableException("503 overloaded"),
        GeminiAllApiKeysExhaustedException("keys exhausted"),
    ],
)
def test_non_censorship_errors_are_not_split(error):
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        raise error

    service = _service(respond)
    with pytest.raises(type(error)):
        asyncio.run(service._translate_integrity_chunk_with_retry(_units(8)))

    assert len(calls) == 1  # 분할 없이 한 번에 끝난다
