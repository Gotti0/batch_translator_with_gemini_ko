"""
test/test_domain/test_pagefold_integration.py
Tests for PageFold integration in TranslationService.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from domain.translation_service import TranslationService
from core.dtos import GlossaryEntryDTO
from google.genai import types as genai_types


@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value="번역된 텍스트입니다.\\n두 번째 줄입니다.")
    return client


@pytest.fixture
def base_config():
    return {
        "model_name": "gemini-2.0-flash",
        "target_translation_language": "ko",
        "enable_pagefold": True,
        "pagefold_mode": "reference",
        "pagefold_font_size": 1.0,
        "enable_prefill_translation": False,
        "prompts": "Translate: {{slot}}"
    }


@pytest.mark.asyncio
async def test_pagefold_glossary_pdf_injection(mock_gemini_client, base_config):
    service = TranslationService(mock_gemini_client, base_config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="アルトリア", translated_keyword="알트리아", target_language="ko", occurrence_count=10),
        GlossaryEntryDTO(keyword="エクスカリバー", translated_keyword="엑스칼리버", target_language="ko", occurrence_count=5),
    ]

    result = await service.translate_text_async("アルトリアがエクスカ리バーを構えた。")

    # 1. Output newline restoration check
    assert result == "번역된 텍스트입니다.\n두 번째 줄입니다."

    # 2. Check generate_text_async call arguments
    mock_gemini_client.generate_text_async.assert_called_once()
    call_kwargs = mock_gemini_client.generate_text_async.call_args.kwargs

    multimodal_parts = call_kwargs.get("multimodal_parts")
    assert multimodal_parts is not None
    assert len(multimodal_parts) == 1
    assert multimodal_parts[0].inline_data.mime_type == "application/pdf"
    assert multimodal_parts[0].inline_data.data.startswith(b"%PDF-1.7")


@pytest.mark.asyncio
async def test_pagefold_disabled(mock_gemini_client, base_config):
    base_config["enable_pagefold"] = False
    service = TranslationService(mock_gemini_client, base_config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="アルトリア", translated_keyword="알트리아", target_language="ko", occurrence_count=10),
    ]

    result = await service.translate_text_async("테스트 문장")

    call_kwargs = mock_gemini_client.generate_text_async.call_args.kwargs
    multimodal_parts = call_kwargs.get("multimodal_parts")
    assert multimodal_parts is None


@pytest.mark.asyncio
async def test_pagefold_marked_tag_mode(mock_gemini_client, base_config):
    base_config["prompts"] = "참조: <pdf>비밀 설정: 주인공은 용사다.</pdf>\n\n본문: {{slot}}"
    service = TranslationService(mock_gemini_client, base_config)

    await service.translate_text_async("용사가 칼을 들었다.")

    call_kwargs = mock_gemini_client.generate_text_async.call_args.kwargs
    multimodal_parts = call_kwargs.get("multimodal_parts")
    assert multimodal_parts is not None
    assert len(multimodal_parts) >= 1
    # Check that <pdf>...</pdf> was replaced in user prompt
    prompt = call_kwargs.get("prompt")
    user_part_text = prompt[0].parts[0].text
    assert "<pdf>" not in user_part_text
    assert "[첨부된 고밀도 PDF 참조 문서]" in user_part_text


GLOSSARY_PROMPT = "용어집:\n{{glossary_context}}\n\n번역: {{slot}}"


def _user_prompt_text(mock_client) -> str:
    prompt = mock_client.generate_text_async.call_args.kwargs.get("prompt")
    return prompt[-1].parts[0].text


@pytest.mark.asyncio
async def test_pagefold_notice_fills_glossary_context_without_prefill(mock_gemini_client, base_config):
    """PDF 첨부 시 {{glossary_context}}는 '컨텍스트 없음'이 아니라 PageFold 안내문으로 채워져야 한다."""
    from domain.translation_service import PAGEFOLD_GLOSSARY_NOTICE

    mock_gemini_client.supports_pagefold = True
    base_config["prompts"] = GLOSSARY_PROMPT
    service = TranslationService(mock_gemini_client, base_config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="アルトリア", translated_keyword="알트리아", target_language="ko", occurrence_count=10),
    ]

    await service.translate_text_async("アルトリアが剣を構えた。")

    call_kwargs = mock_gemini_client.generate_text_async.call_args.kwargs
    assert call_kwargs.get("multimodal_parts")
    user_text = _user_prompt_text(mock_gemini_client)
    assert PAGEFOLD_GLOSSARY_NOTICE in user_text
    assert "용어집 컨텍스트 없음" not in user_text
    assert "{{glossary_context}}" not in user_text


@pytest.mark.asyncio
async def test_pagefold_notice_fills_glossary_context_prefill_without_slot(mock_gemini_client, base_config):
    """프리필 히스토리에 {{slot}}이 없어 _construct_prompt 경로를 탈 때도 안내문이 쓰여야 한다."""
    from domain.translation_service import PAGEFOLD_GLOSSARY_NOTICE

    mock_gemini_client.supports_pagefold = True
    base_config["prompts"] = GLOSSARY_PROMPT
    base_config["enable_prefill_translation"] = True
    base_config["prefill_cached_history"] = [
        {"role": "user", "parts": ["준비됐나요?"]},
        {"role": "model", "parts": ["네, 준비됐습니다."]},
    ]
    service = TranslationService(mock_gemini_client, base_config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="アルトリア", translated_keyword="알트리아", target_language="ko", occurrence_count=10),
    ]

    await service.translate_text_async("アルトリアが剣を構えた。")

    user_text = _user_prompt_text(mock_gemini_client)
    assert PAGEFOLD_GLOSSARY_NOTICE in user_text
    assert "용어집 컨텍스트 없음" not in user_text


@pytest.mark.asyncio
async def test_dynamic_injection_unchanged_when_pagefold_disabled(mock_gemini_client, base_config):
    """PageFold 비활성화 시에는 기존 동적 용어집 주입이 그대로 동작해야 한다."""
    from domain.translation_service import PAGEFOLD_GLOSSARY_NOTICE

    mock_gemini_client.supports_pagefold = True
    base_config["prompts"] = GLOSSARY_PROMPT
    base_config["enable_pagefold"] = False
    base_config["enable_dynamic_glossary_injection"] = True
    service = TranslationService(mock_gemini_client, base_config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="アルトリア", translated_keyword="알트리아", target_language="ko", occurrence_count=10),
    ]

    await service.translate_text_async("アルトリアが剣を構えた。")

    user_text = _user_prompt_text(mock_gemini_client)
    assert PAGEFOLD_GLOSSARY_NOTICE not in user_text
    assert "알트리아" in user_text
