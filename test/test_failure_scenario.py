"""빈 번역 결과가 성공으로 취급되지 않는지 검증한다.

원래 이 파일은 `translate_text_with_content_safety_retry`가 빈 문자열을 돌려주도록 목을
심고, 메타데이터에 완료 기록이 남지 않는지를 보는 수동 스크립트였다. 비동기 마이그레이션
이후 유효하지 않다는 주석과 함께 비활성화를 시도했으나 본문 일부만 주석 처리돼
`IndentationError`로 수집되지 않았다.

다시 쓰면서 검증 지점을 옮겼다. 메인 번역 루프는 반환값을 검사하지 않고 성공으로
기록하므로(app_service.py의 `success = True`), 빈 결과를 막는 실제 방어선은 그 아래
`translate_text_async`다. 여기서 빈 응답과 None을 예외로 바꾸기 때문에 위로 빈 문자열이
올라가지 않는다. 그 성질이 깨지면 빈 번역문이 조용히 완료로 기록되므로 이 지점을 고정한다.

메인 루프에 방어적 검사를 두는 편이 나을 수 있으나, 그것은 이 테스트의 범위가 아니다.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import BtgTranslationException
from domain.translation_service import TranslationService


def _service(return_value):
    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value=return_value)
    return TranslationService(
        gemini_client=client,
        config={"model_name": "gemini-test", "use_content_safety_retry": False},
    )


@pytest.mark.parametrize("empty_response", ["", "   ", "\n\n"])
def test_empty_translation_is_rejected(empty_response):
    """공백뿐인 응답은 번역 성공이 아니라 오류다."""
    service = _service(empty_response)

    with pytest.raises(BtgTranslationException) as excinfo:
        asyncio.run(service.translate_text_async("번역할 내용이 있는 원문"))

    assert "콘텐츠 안전 문제" in str(excinfo.value)


def test_none_response_is_rejected():
    """응답 자체가 없으면 빈 문자열을 반환하지 않고 오류로 끝낸다."""
    service = _service(None)

    with pytest.raises(BtgTranslationException):
        asyncio.run(service.translate_text_async("번역할 내용이 있는 원문"))


def test_empty_input_returns_empty_without_calling_api():
    """원문이 비어 있으면 그것은 오류가 아니라 빈 결과다. API를 부르지 않는다."""
    service = _service("무엇이든")

    result = asyncio.run(service.translate_chunk_async("   "))

    assert result == ""
    service.gemini_client.generate_text_async.assert_not_called()
