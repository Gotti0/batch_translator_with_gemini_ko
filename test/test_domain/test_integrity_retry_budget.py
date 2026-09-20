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
    # 깊이를 재는 테스트가 최소 크기에 먼저 걸리지 않도록 0으로 둔다.
    base = {"model_name": "gemini-test", "min_content_safety_chunk_size": 0}
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


def test_json_parse_failure_split_follows_the_same_switch():
    """파싱 실패 분할도 검열 분할과 같은 스위치에 묶인다."""
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return None  # 파싱 실패로 취급되는 응답

    service = _service(respond, use_content_safety_retry=False)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(4)))

    assert len(calls) == 1  # 분할하지 않는다
    assert all(result[str(i)] == f"line{i}" for i in range(4))  # 원문을 남긴다


def test_json_parse_failure_still_splits_when_switch_is_on():
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return None

    service = _service(respond)
    asyncio.run(service._translate_integrity_chunk_with_retry(_units(8)))

    assert len(calls) == 7  # 1 + 2 + 4, 깊이 2에서 멈춘다


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


def test_min_chunk_size_stops_the_split():
    """GUI '최소 청크 크기'가 무결성 분할에도 듣는다."""
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        raise GeminiContentSafetyException("censored")

    # 8항목 x "line0" 5글자 = 40글자. 최소 크기 100에 걸려 쪼개기 전에 멈춘다.
    service = _service(respond, min_content_safety_chunk_size=100)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(8)))

    assert len(calls) == 1
    assert all(result[str(i)] == f"line{i}" for i in range(8))


def test_max_split_attempts_setting_drives_integrity_depth():
    """GUI '최대 분할 시도'가 무결성 분할 깊이를 정한다."""
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        raise GeminiContentSafetyException("censored")

    service = _service(respond, max_content_safety_split_attempts=1)
    asyncio.run(service._translate_integrity_chunk_with_retry(_units(8)))

    assert len(calls) == 3  # 1 + 2, 깊이 1에서 멈춘다


def test_empty_translation_counts_as_missing():
    """빈 번역문은 받은 것이 아니라 누락이다. 키만 있으면 재시도가 걸리지 않았다."""
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            # 두 번째 항목이 빈 문자열로 돌아온다.
            return [
                {"id": "0", "translated_text": "t0"},
                {"id": "1", "translated_text": ""},
            ]
        return [{"id": "1", "translated_text": "t1"}]

    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(2)))

    assert len(calls) == 2  # 빈 항목만 다시 물었다
    assert result["1"] == "t1"


def test_empty_translation_falls_back_to_source_at_limit():
    """재시도 한도에 걸리면 빈 문자열이 아니라 원문이 남는다."""

    async def respond(**kwargs):
        return [
            {"id": "0", "translated_text": "t0"},
            {"id": "1", "translated_text": "   "},
        ]

    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(_units(2)))

    assert result["1"] == "line1"


def test_blank_source_may_translate_to_empty():
    """원문이 공백뿐이면 빈 번역문이 정상이다. 이것까지 재시도하면 안 된다."""
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        return [
            {"id": "0", "translated_text": "t0"},
            {"id": "1", "translated_text": ""},
        ]

    chunk = [
        TranslationUnit(id="0", text="line0"),
        TranslationUnit(id="1", text="   "),
    ]
    service = _service(respond)
    result = asyncio.run(service._translate_integrity_chunk_with_retry(chunk))

    assert len(calls) == 1
    assert result["1"] == ""
