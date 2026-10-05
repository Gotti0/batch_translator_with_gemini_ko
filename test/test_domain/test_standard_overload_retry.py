"""표준 모드도 과부하(503)가 풀릴 때까지 청크를 다시 시도하는지 확인한다.

예전에는 503 재시도까지 실패하면 그 청크만 실패로 남기고 다음 청크로 넘어갔다. 과부하가 길면
남은 청크가 전부 "실패 문구 + 원문"으로 채워진 채 병합돼 완료로 끝났다(실호출 검증에서 재현).
지금은 무결성·EPUB과 같은 방침으로 같은 청크를 성공할 때까지 기다린다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import BtgApiClientException, BtgTranslationException
from domain.translation_service import TranslationService
from infrastructure.gemini_client import GeminiServiceUnavailableException

TEXT = "\n".join(f"문장{i:02d} 입니다. 내용을 충분히 길게 만들기 위한 문장입니다." for i in range(20))


def _overloaded() -> BtgApiClientException:
    # translate_text_async가 과부하를 올리는 모양 그대로
    return BtgApiClientException(
        "API 호출 중 오류가 발생했습니다",
        original_exception=GeminiServiceUnavailableException("모델 과부하(503)로 2회 시도했으나 실패"),
    )


def _service(fake_translate, **config):
    service = TranslationService(MagicMock(), {"model_name": "m", **config})
    service.translate_text_async = fake_translate
    return service


@pytest.mark.parametrize("use_content_safety_retry", [True, False])
def test_overloaded_chunk_is_retried_until_it_succeeds(use_content_safety_retry):
    calls = []

    async def overloaded_twice(text, stream=False):
        calls.append(text)
        if len(calls) <= 2:
            raise _overloaded()
        return "번역 완료"

    service = _service(overloaded_twice, use_content_safety_retry=use_content_safety_retry)

    assert asyncio.run(service.translate_chunk_async(TEXT)) == "번역 완료"
    assert calls == [TEXT, TEXT, TEXT]


def test_overload_during_safety_split_retries_the_chunk_instead_of_leaving_a_placeholder():
    """검열 분할 중 서브 청크가 과부하를 만나도 "[서브 청크 N 번역 오류]"로 삼키지 않는다."""
    calls = []

    async def respond(text, stream=False):
        calls.append(text)
        if len(calls) == 1:
            raise BtgTranslationException("콘텐츠 안전 문제로 번역할 수 없습니다. (테스트)")
        if len(calls) == 2:
            raise _overloaded()  # 첫 서브 청크
        return f"T({text[:6]})"

    service = _service(respond, use_content_safety_retry=True, min_content_safety_chunk_size=20)

    result = asyncio.run(service.translate_chunk_async(TEXT))

    assert "서브 청크" not in result
    assert calls[2] == TEXT  # 과부하 뒤에는 청크 전체를 처음부터 다시 시도한다
    assert result.startswith("T(")


def test_non_overload_errors_still_fail_the_chunk():
    """과부하가 아닌 오류까지 기다리지는 않는다."""
    calls = []

    async def broken(text, stream=False):
        calls.append(text)
        raise BtgApiClientException("잘못된 API 요청입니다: 400")

    service = _service(broken, use_content_safety_retry=False)

    with pytest.raises(BtgTranslationException):
        asyncio.run(service.translate_chunk_async(TEXT))
    assert len(calls) == 1


def test_cancel_while_waiting_out_overload():
    async def always_overloaded(text, stream=False):
        await asyncio.sleep(0.01)
        raise _overloaded()

    service = _service(always_overloaded, use_content_safety_retry=False)

    async def scenario():
        task = asyncio.ensure_future(service.translate_chunk_async(TEXT))
        await asyncio.sleep(0.05)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
