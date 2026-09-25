"""
실시간·배치 번역이 공유하는 요청 조립 함수 테스트.

- TranslationService.build_translation_request / finalize_translation_text
- GeminiClient.build_sdk_contents / build_generate_config
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from google.genai import types as genai_types

from core.dtos import GlossaryEntryDTO
from domain.translation_service import PAGEFOLD_GLOSSARY_NOTICE, TranslationService
from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException
from utils.pdf_packer import PDF_NEWLINE_MARKER_DIRECTIVE


def _texts(content):
    return [p.text for p in content.parts if getattr(p, "text", None) is not None]


@pytest.fixture
def client():
    c = MagicMock()
    c.supports_pagefold = True
    c.generate_text_async = AsyncMock(return_value="번역 결과")
    return c


@pytest.fixture
def config():
    return {
        "model_name": "gemini-3.8-flash",
        "target_translation_language": "ko",
        "enable_pagefold": False,
        "enable_prefill_translation": False,
        "prompts": "Translate: {{slot}}\nGlossary: {{glossary_context}}",
        "temperature": 0.5,
        "top_p": 0.8,
        "thinking_level": "low",
    }


def test_plain_prompt(client, config):
    service = TranslationService(client, config)
    req = service.build_translation_request("原文です")

    assert len(req.contents) == 1
    assert req.contents[0].role == "user"
    assert _texts(req.contents[0]) == ["Translate: 原文です\nGlossary: 용어집 컨텍스트 없음 (주입 비활성화 또는 해당 항목 없음)"]
    assert req.system_instruction is None
    assert req.multimodal_parts is None


def test_dynamic_glossary_injection(client, config):
    config["enable_dynamic_glossary_injection"] = True
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
    ]
    req = service.build_translation_request("勇者が来た")
    assert "勇者 -> 용사" in _texts(req.contents[0])[0]


def test_prefill_history_with_slot(client, config):
    config.update({
        "enable_prefill_translation": True,
        "prefill_system_instruction": "너는 번역가다.",
        "prefill_cached_history": [
            {"role": "user", "parts": ["다음을 번역: {{slot}}"]},
            {"role": "model", "parts": ["네."]},
        ],
    })
    service = TranslationService(client, config)
    req = service.build_translation_request("原文")

    assert req.system_instruction == "너는 번역가다."
    assert [c.role for c in req.contents] == ["user", "model", "user"]
    assert _texts(req.contents[0]) == ["다음을 번역: 原文"]
    # 마지막이 model이면 이어쓰기를 위해 빈 user 턴을 붙인다
    assert _texts(req.contents[-1]) == [" "]


def test_prefill_history_without_slot_appends_prompt(client, config):
    config.update({
        "enable_prefill_translation": True,
        "prefill_system_instruction": "sys",
        "prefill_cached_history": [{"role": "user", "parts": ["예시"]}, {"role": "model", "parts": ["예시 번역"]}],
    })
    service = TranslationService(client, config)
    req = service.build_translation_request("原文")
    assert [c.role for c in req.contents] == ["user", "model", "user"]
    assert _texts(req.contents[-1])[0].startswith("Translate: 原文")


def test_prefill_history_with_slot_and_glossary(client, config):
    """히스토리가 {{slot}}과 {{glossary_context}}를 모두 채우면 메인 템플릿은 필요 없다"""
    config.update({
        "enable_prefill_translation": True,
        "enable_dynamic_glossary_injection": True,
        "prompts": ":",
        "prefill_cached_history": [
            {"role": "user", "parts": ["<main>{{slot}}</main>\n<info>{{glossary_context}}</info>"]},
            {"role": "model", "parts": ["네."]},
        ],
    })
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
    ]
    req = service.build_translation_request("勇者が来た")

    first = _texts(req.contents[0])[0]
    assert "<main>勇者が来た</main>" in first
    assert "勇者 -> 용사" in first
    assert "{{" not in first


def test_prefill_history_with_glossary_only_still_sends_chunk(client, config):
    """히스토리에 {{glossary_context}}만 있으면 원문은 메인 템플릿으로 보낸다"""
    config.update({
        "enable_prefill_translation": True,
        "enable_dynamic_glossary_injection": True,
        "prompts": "Translate: {{slot}}",
        "prefill_cached_history": [
            {"role": "user", "parts": ["용어집: {{glossary_context}}"]},
            {"role": "model", "parts": ["네."]},
        ],
    })
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
    ]
    req = service.build_translation_request("勇者が来た")

    assert [c.role for c in req.contents] == ["user", "model", "user"]
    assert "勇者 -> 용사" in _texts(req.contents[0])[0]
    assert _texts(req.contents[-1]) == ["Translate: 勇者が来た"]


def test_missing_slot_hints_disabled_prefill(client, config):
    """프리필이 꺼진 채 히스토리에만 {{slot}}이 있으면 오류가 원인을 알려 준다"""
    from core.exceptions import BtgTranslationException

    config.update({
        "prompts": ":",
        "prefill_cached_history": [{"role": "user", "parts": ["{{slot}}"]}],
    })
    service = TranslationService(client, config)
    with pytest.raises(BtgTranslationException, match="프리필 번역이 꺼져"):
        service.build_translation_request("原文")


def test_pagefold_glossary_pdf_and_directive(client, config):
    config["enable_pagefold"] = True
    config["pagefold_mode"] = "reference"
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
    ]
    req = service.build_translation_request("勇者が来た")

    assert req.multimodal_parts and req.multimodal_parts[0].inline_data.mime_type == "application/pdf"
    assert req.system_instruction == PDF_NEWLINE_MARKER_DIRECTIVE
    # {{glossary_context}}에는 PDF 첨부 안내문이 들어간다
    assert PAGEFOLD_GLOSSARY_NOTICE in _texts(req.contents[0])[0]
    # 같은 세션에서는 같은 PDF 파트 객체를 재사용한다 (배치에서 중복 업로드를 피하는 근거)
    assert service.build_translation_request("別の文").multimodal_parts[0] is req.multimodal_parts[0]


def test_pdf_tags_stripped_for_non_pagefold_client(client, config):
    client.supports_pagefold = False
    config["prompts"] = "<pdf>참고 자료</pdf>\nTranslate: {{slot}}"
    service = TranslationService(client, config)
    req = service.build_translation_request("原文")
    assert _texts(req.contents[0]) == ["참고 자료\nTranslate: 原文"]
    assert req.multimodal_parts is None


@pytest.mark.asyncio
async def test_translate_text_async_sends_built_request(client, config):
    """실시간 경로가 build_translation_request의 결과를 그대로 보낸다"""
    service = TranslationService(client, config)
    expected = service.build_translation_request("原文")

    assert await service.translate_text_async("原文") == "번역 결과"
    kwargs = client.generate_text_async.call_args.kwargs
    assert [_texts(c) for c in kwargs["prompt"]] == [_texts(c) for c in expected.contents]
    assert kwargs["system_instruction_text"] == expected.system_instruction
    assert kwargs["generation_config_dict"] == {"temperature": 0.5, "top_p": 0.8, "thinking_level": "low"}


def test_finalize_translation_text(client, config):
    service = TranslationService(client, config)
    assert service.finalize_translation_text("원문", "  번역  ") == "번역"
    with pytest.raises(GeminiContentSafetyException):
        service.finalize_translation_text("원문", None)
    with pytest.raises(GeminiContentSafetyException):
        service.finalize_translation_text("원문", "   ")
    config["enable_pagefold"] = True
    assert service.finalize_translation_text("원문", "a\\nb") == "a\nb"


def test_build_sdk_contents_prepends_multimodal_parts():
    pdf = genai_types.Part.from_bytes(data=b"%PDF-1.7", mime_type="application/pdf")
    contents = GeminiClient.build_sdk_contents("hello", [pdf])
    assert len(contents) == 1 and contents[0].parts[0] is pdf

    history = [
        genai_types.Content(role="model", parts=[genai_types.Part.from_text(text="m")]),
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="u")]),
    ]
    contents = GeminiClient.build_sdk_contents(history, [pdf])
    assert contents[1].parts[0] is pdf


def test_build_generate_config_realtime_vs_batch():
    client = GeminiClient(auth_credentials="test_api_key")
    pdf = genai_types.Part.from_bytes(data=b"%PDF-1.7", mime_type="application/pdf")

    realtime = client.build_generate_config(
        "gemini-3.8-flash", {"temperature": 0.5, "thinking_level": "low"},
        system_instruction_text="sys", multimodal_parts=[pdf],
    )
    batch = client.build_generate_config(
        "gemini-3.8-flash", {"temperature": 0.5, "thinking_level": "low"},
        system_instruction_text="sys", multimodal_parts=[pdf], for_batch=True,
    )

    for cfg in (realtime, batch):
        assert cfg.temperature == 0.5
        assert cfg.system_instruction == "sys"
        assert cfg.thinking_config.thinking_level.value.lower() == "low"
        assert cfg.media_resolution == genai_types.MediaResolution.MEDIA_RESOLUTION_LOW
        assert len(cfg.safety_settings) == 5
        assert all(s.threshold == genai_types.HarmBlockThreshold.BLOCK_NONE for s in cfg.safety_settings)

    assert realtime.http_options is not None
    assert realtime.automatic_function_calling.disable is True
    assert batch.http_options is None
    assert batch.automatic_function_calling is None


def test_semantic_glossary_entries_added_when_memory_enabled(client, config):
    """키워드가 원문에 없어도 번역 기억이 찾은 의미 기반 용어가 용어집 컨텍스트에 더해진다"""
    config.update({"enable_dynamic_glossary_injection": True, "enable_translation_memory": True})
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
        GlossaryEntryDTO(keyword="リリア", translated_keyword="릴리아", target_language="ko", occurrence_count=2),
    ]
    memory = MagicMock()
    memory.search_glossary.return_value = ["リリア", "없는키워드"]
    from domain.memory_graph import MemoryRecall
    memory.recall.return_value = MemoryRecall(None, [], [], [])
    memory.format_recall.return_value = ""
    service.translation_memory = memory

    text = _texts(service.build_translation_request("勇者が来た").contents[0])[0]
    assert "勇者 -> 용사" in text  # 키워드 일치
    assert "リリア -> 릴리아" in text  # 의미 기반 (원문에 'リリア' 없음)

    config["enable_translation_memory"] = False
    text = _texts(service.build_translation_request("勇者が来た").contents[0])[0]
    assert "リリア" not in text


@pytest.mark.asyncio
async def test_integrity_chunk_uses_memory_with_plain_text(client, config):
    """무결성 모드: 기억 검색은 JSON이 아닌 원문 줄로 하고, 의미 기반 용어와 기억 블록이 들어간다"""
    from core.dtos import TranslationUnit
    from domain.memory_graph import MemoryRecall

    config.update({"enable_dynamic_glossary_injection": True, "enable_translation_memory": True})
    client.generate_text_async = AsyncMock(return_value=[{"id": "0", "translated_text": "용사가 왔다"}])
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="リリア", translated_keyword="릴리아", target_language="ko", occurrence_count=2),
    ]
    memory = MagicMock()
    memory.search_glossary.return_value = ["リリア"]
    memory.recall.return_value = MemoryRecall(None, [], [], [])
    memory.format_recall.return_value = "<translation_memory>기억</translation_memory>"
    service.translation_memory = memory

    chunk = [TranslationUnit(id="0", text="勇者が来た"), TranslationUnit(id="1", text="")]
    assert await service._translate_integrity_chunk_with_retry(chunk) == {"0": "용사가 왔다"}

    assert memory.search_glossary.call_args.args[0] == "勇者が来た\n"
    assert memory.recall.call_args.args[0] == "勇者が来た\n"
    kwargs = client.generate_text_async.call_args.kwargs
    user_text = _texts(kwargs["prompt"][-1])[0]
    assert "リリア -> 릴리아" in user_text
    assert kwargs["system_instruction_text"].endswith("<translation_memory>기억</translation_memory>")
