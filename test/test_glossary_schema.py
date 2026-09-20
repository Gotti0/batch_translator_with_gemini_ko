"""용어집 추출이 API와 주고받는 형식을 고정한다.

예전에는 `GLOSSARY_RESPONSE_SCHEMA`라는 손으로 쓴 genai 스키마가 있었고 이 테스트는 그
구조를 확인했다. Gemini API 마이그레이션 때 그 상수가 사라지고 `list[ApiGlossaryTerm]`
Pydantic 모델이 response_schema로 넘어가게 바뀌었다. 검증 대상을 그 모델로 옮긴다.

필드 이름은 모델이 채워 보내는 JSON의 키이자 용어집 파일의 키이기도 해서, 바뀌면 추출
결과를 읽는 쪽이 조용히 빈 값을 얻는다.
"""
import pytest
from pydantic import ValidationError

from domain.glossary_service import ApiGlossaryTerm, SimpleGlossaryService


class _DummyGeminiClient:
    pass


def test_api_glossary_term_fields():
    """응답 모델의 필드 이름과 타입이 계약이다."""
    fields = ApiGlossaryTerm.model_fields

    assert set(fields) == {
        "keyword",
        "translated_keyword",
        "target_language",
        "occurrence_count",
    }
    assert fields["keyword"].annotation is str
    assert fields["translated_keyword"].annotation is str
    assert fields["target_language"].annotation is str
    assert fields["occurrence_count"].annotation is int


def test_api_glossary_term_requires_every_field():
    """네 필드 모두 필수다. 하나라도 빠진 응답은 용어집 항목이 되지 못한다."""
    for missing in ApiGlossaryTerm.model_fields:
        payload = {
            "keyword": "term",
            "translated_keyword": "용어",
            "target_language": "ko",
            "occurrence_count": 1,
        }
        del payload[missing]

        with pytest.raises(ValidationError):
            ApiGlossaryTerm(**payload)


def test_api_terms_convert_to_dto():
    """API 응답 모델이 내부 DTO로 옮겨질 때 네 필드가 그대로 따라간다."""
    service = SimpleGlossaryService(gemini_client=_DummyGeminiClient(), config={})
    api_terms = [
        ApiGlossaryTerm(
            keyword="source-term",
            translated_keyword="translation",
            target_language="ko",
            occurrence_count=2,
        )
    ]

    entries = service._parse_api_glossary_terms_to_dto(api_terms)

    assert len(entries) == 1
    assert entries[0].keyword == "source-term"
    assert entries[0].translated_keyword == "translation"
    assert entries[0].target_language == "ko"
    assert entries[0].occurrence_count == 2


def test_non_api_term_items_are_skipped():
    """형식이 어긋난 항목은 변환을 멈추지 않고 건너뛴다."""
    service = SimpleGlossaryService(gemini_client=_DummyGeminiClient(), config={})
    api_terms = [
        {"keyword": "dict-not-model"},
        ApiGlossaryTerm(
            keyword="valid",
            translated_keyword="유효",
            target_language="ko",
            occurrence_count=1,
        ),
    ]

    entries = service._parse_api_glossary_terms_to_dto(api_terms)

    assert [e.keyword for e in entries] == ["valid"]


def test_dict_list_converts_to_dto():
    """용어집 파일에서 읽은 딕셔너리도 같은 DTO가 된다."""
    service = SimpleGlossaryService(gemini_client=_DummyGeminiClient(), config={})

    entries = service._parse_dict_list_to_dto(
        [
            {
                "keyword": "source-term",
                "translated_keyword": "translation",
                "target_language": "ko",
                "occurrence_count": 2,
            }
        ]
    )

    assert len(entries) == 1
    assert entries[0].keyword == "source-term"
