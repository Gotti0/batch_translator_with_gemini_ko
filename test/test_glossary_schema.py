from google.genai import types as genai_types

from domain.glossary_service import (
    ApiGlossaryTerm,
    GLOSSARY_RESPONSE_SCHEMA,
    SimpleGlossaryService,
)


class _DummyGeminiClient:
    pass


def test_glossary_response_schema_structure():
    assert GLOSSARY_RESPONSE_SCHEMA.type == genai_types.Type.ARRAY
    assert GLOSSARY_RESPONSE_SCHEMA.items is not None
    assert GLOSSARY_RESPONSE_SCHEMA.items.type == genai_types.Type.OBJECT
    assert set(GLOSSARY_RESPONSE_SCHEMA.items.required or []) == {
        "keyword",
        "translated_keyword",
        "target_language",
        "occurrence_count",
    }


def test_convert_raw_items_to_api_terms_handles_dicts():
    service = SimpleGlossaryService(gemini_client=_DummyGeminiClient(), config={})
    raw_terms = [
        {
            "keyword": "source-term",
            "translated_keyword": "translation",
            "target_language": "ko",
            "occurrence_count": 2,
        }
    ]

    converted = service._convert_raw_items_to_api_terms(raw_terms)

    assert len(converted) == 1
    assert isinstance(converted[0], ApiGlossaryTerm)
    assert converted[0].keyword == "source-term"
