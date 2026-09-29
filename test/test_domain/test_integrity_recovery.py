import pytest
from unittest.mock import AsyncMock, MagicMock

from core.dtos import TranslationUnit
from domain.translation_service import TranslationService


def test_recover_integrity_units_unescaped_quotes():
    """번역문 내부에 이스케이프되지 않은 큰따옴표가 있어도 정규식 Fallback으로 정상 복구한다."""
    raw_text = """[
      {"id": 0, "translated_text": "첫 번째 줄"},
      {"id": 1, "translated_text": "그녀는 "어머나!"라고 소리쳤다."},
      {"id": 2, "translated_text": "세 번째 줄"}
    ]"""

    recovered = TranslationService._recover_integrity_units_from_raw_text(raw_text)
    assert recovered is not None
    assert len(recovered) == 3
    assert recovered[0] == {"id": "0", "translated_text": "첫 번째 줄"}
    assert recovered[1] == {"id": "1", "translated_text": '그녀는 "어머나!"라고 소리쳤다.'}
    assert recovered[2] == {"id": "2", "translated_text": "세 번째 줄"}


def test_recover_integrity_units_missing_commas():
    """객체 간 쉼표가 누락되어도 모든 단위를 정상 복구한다."""
    raw_text = """[
      {"id": 10, "translated_text": "10번 문장"}
      {"id": 11, "translated_text": "11번 문장"}
    ]"""

    recovered = TranslationService._recover_integrity_units_from_raw_text(raw_text)
    assert recovered is not None
    assert len(recovered) == 2
    assert recovered[0]["id"] == "10"
    assert recovered[1]["id"] == "11"


def test_recover_integrity_units_alternative_keys_and_string_ids():
    """text 또는 translation 키와 문자열 ID("0")도 정상 인식한다."""
    raw_text = """
    Here is your translation:
    [
      {"id": "0", "text": "텍스트 키 사용"},
      {"id": 1, "translation": "번역 키 사용"}
    ]
    """

    recovered = TranslationService._recover_integrity_units_from_raw_text(raw_text)
    assert recovered is not None
    assert len(recovered) == 2
    assert recovered[0] == {"id": "0", "translated_text": "텍스트 키 사용"}
    assert recovered[1] == {"id": "1", "translated_text": "번역 키 사용"}


@pytest.mark.asyncio
async def test_translate_integrity_chunk_recovers_from_broken_json():
    """json.loads가 실패하는 응답을 반환해도 binary split 없이 정규식 fallback으로 복구하여 결과를 반환한다."""
    broken_json = """[
      {"id": 0, "translated_text": "정상 문장"},
      {"id": 1, "translated_text": "그는 "안녕"이라고 말했다."}
    ]"""

    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value=broken_json)

    service = TranslationService(
        gemini_client=client,
        config={"model_name": "test-model", "use_content_safety_retry": True}
    )

    chunk = [
        TranslationUnit(id="0", text="Sentence 0"),
        TranslationUnit(id="1", text="Sentence 1"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)

    assert result["0"] == "정상 문장"
    assert result["1"] == '그는 "안녕"이라고 말했다.'
    # split이 발생하지 않고 단 1회 호출로 모두 복구되었음을 확인
    assert client.generate_text_async.call_count == 1


@pytest.mark.asyncio
async def test_translate_integrity_chunk_passes_response_schema_and_accepts_translated_units():
    """무결성 번역 호출 시 response_schema에 list[TranslatedUnit]를 전달하고, 모델/클라이언트가 반환한 TranslatedUnit 인스턴스를 정상 처리한다."""
    from core.dtos import TranslatedUnit

    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value=[
        TranslatedUnit(id="0", translated_text="직접 번역 0"),
        TranslatedUnit(id="1", translated_text="직접 번역 1"),
    ])

    service = TranslationService(
        gemini_client=client,
        config={"model_name": "test-model"}
    )

    chunk = [
        TranslationUnit(id="0", text="Sentence 0"),
        TranslationUnit(id="1", text="Sentence 1"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)

    assert result["0"] == "직접 번역 0"
    assert result["1"] == "직접 번역 1"

    call_config = client.generate_text_async.call_args.kwargs["generation_config_dict"]
    assert call_config["response_schema"] == list[TranslatedUnit]
    assert call_config["response_mime_type"] == "application/json"

