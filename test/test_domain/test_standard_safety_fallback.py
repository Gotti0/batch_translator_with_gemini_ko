# test/test_domain/test_standard_safety_fallback.py
import asyncio
from unittest.mock import MagicMock
import pytest

from core.exceptions import BtgTranslationException
from domain.translation_service import TranslationService


def _create_mock_service(fail_phrase=None, client_error_phrase=None):
    mock_client = MagicMock()
    config = {
        "model_name": "test-model",
        "max_workers": 1,
    }
    service = TranslationService(gemini_client=mock_client, config=config)

    async def fake_translate(text: str, stream: bool = False):
        if fail_phrase and fail_phrase in text:
            raise BtgTranslationException("콘텐츠 안전 문제로 인해 차단되었습니다.")
        if client_error_phrase and client_error_phrase in text:
            raise RuntimeError("API unexpected error")
        return f"[번역됨] {text.strip()}"

    service.translate_text_async = fake_translate
    return service


@pytest.mark.asyncio
async def test_recursive_splitting_preserves_raw_subchunk_on_final_safety_failure():
    """
    서브 청크 중 특정 문장이 검열(안전 필터)에 지속적으로 걸려 최종 실패할 때,
    에러 안내 문구 대신 해당 서브 청크의 원문이 그대로 보존되는지 검증합니다.
    """
    safe_sentence1 = "첫 번째 안전한 문장입니다. 오늘도 좋은 하루입니다."
    unsafe_sentence = "이 문장은 민감한 단어가 포함되어 검열에 걸리는 문장입니다."
    safe_sentence2 = "두 번째 안전한 문장입니다. 번역이 원활하게 진행됩니다."
    full_text = f"{safe_sentence1}\n\n{unsafe_sentence}\n\n{safe_sentence2}"

    service = _create_mock_service(fail_phrase="민감한 단어")

    # max_split_attempts=3 (기본값)으로 설정하여 2차 분할에서 safe_sentence1과 unsafe_sentence가 분리되도록 함
    result = await service.translate_text_with_content_safety_retry_async(
        full_text, max_split_attempts=3, min_chunk_size=10
    )

    # 1. 안전한 문장은 정상 번역되었는지 확인
    assert f"[번역됨] {safe_sentence1}" in result
    assert f"[번역됨] {safe_sentence2}" in result

    # 2. 에러 플레이스홀더 문구가 없어야 함
    assert "[서브 청크" not in result
    assert "[번역 오류" not in result

    # 3. 검열된 문장은 원문 그대로 보존되어 있어야 함
    assert unsafe_sentence in result


@pytest.mark.asyncio
async def test_recursive_splitting_preserves_raw_when_max_attempts_exceeded():
    """
    최대 분할 시도 횟수를 초과한 경우 에러 메시지가 아닌 원문 text_chunk를 반환해야 합니다.
    """
    text = "더 이상 쪼갤 수 없는 깊이에 도달한 원문 텍스트입니다."
    service = _create_mock_service()

    result = await service._translate_with_recursive_splitting_async(
        text_chunk=text,
        max_split_attempts=2,
        min_chunk_size=10,
        current_attempt=3  # max_split_attempts(2) 초과
    )

    assert result == text
    assert "번역 오류" not in result


@pytest.mark.asyncio
async def test_recursive_splitting_preserves_raw_when_min_size_reached():
    """
    최소 청크 크기 이하에 도달한 경우 에러 메시지가 아닌 원문 text_chunk를 반환해야 합니다.
    """
    text = "짧은 문장."
    service = _create_mock_service()

    result = await service._translate_with_recursive_splitting_async(
        text_chunk=text,
        max_split_attempts=3,
        min_chunk_size=50,  # len(text) < 50
        current_attempt=1
    )

    assert result == text
    assert "번역 오류" not in result


@pytest.mark.asyncio
async def test_recursive_splitting_preserves_raw_on_unexpected_error():
    """
    서브 청크에서 예기치 못한 예외가 발생했을 때 에러 안내 문구 대신 원문을 보존해야 합니다.
    """
    text1 = "정상 서브 청크 1번"
    text2 = "예외 서브 청크 2번 (오류 발생)"
    full_text = f"{text1}\n\n{text2}"

    service = _create_mock_service(fail_phrase=None, client_error_phrase="오류 발생")

    result = await service.translate_text_force_split_async(
        full_text, max_split_attempts=2, min_chunk_size=5, split_level=1
    )

    # 안전 문장은 번역됨
    assert f"[번역됨] {text1}" in result
    # 오류 서브 청크는 원문 그대로 유지됨
    assert text2 in result
    assert "[서브 청크" not in result
