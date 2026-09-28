"""청크마다 남기는 용어집 주입 요약 줄이 표준·무결성 모드에서 같은지 확인한다.

무결성 모드는 용어집을 주입하면서도 로그를 남기지 않아, 사용자가 로그만 보고 주입이 꺼진 것으로 읽었다.
요약 한 줄은 INFO, 주입한 용어 목록은 DEBUG에 둔다.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dtos import GlossaryEntryDTO, TranslationUnit
from domain.translation_service import TranslationService

LOGGER = "domain.translation_service"


def _service(**overrides):
    client = MagicMock()
    client.supports_pagefold = False
    client.generate_text_async = AsyncMock(return_value=[{"id": "0", "translated_text": "용사가 왔다"}])
    config = {
        "model_name": "gemini-test",
        "target_translation_language": "ko",
        "enable_pagefold": False,
        "enable_prefill_translation": False,
        "enable_dynamic_glossary_injection": True,
        "max_glossary_entries_per_chunk_injection": 1,
        "prompts": "Translate: {{slot}}\nGlossary: {{glossary_context}}",
    }
    config.update(overrides)
    service = TranslationService(client, config)
    service.glossary_entries_for_injection = [
        GlossaryEntryDTO(keyword="勇者", translated_keyword="용사", target_language="ko", occurrence_count=3),
        GlossaryEntryDTO(keyword="魔王", translated_keyword="마왕", target_language="ko", occurrence_count=2),
        GlossaryEntryDTO(keyword="聖女", translated_keyword="성녀", target_language="ko", occurrence_count=1),
    ]
    return service


def _standard(service, text):
    service.build_translation_request(text)


def _integrity(service, text):
    asyncio.run(service._translate_integrity_chunk_with_retry([TranslationUnit(id="0", text=text)]))


def _glossary_records(caplog):
    """청크 단위 용어집 로그. 예전 표준 모드의 두 줄(주입 활성화됨, 컨텍스트 생성됨)도 잡아 중복을 드러낸다."""
    def per_chunk(msg):
        return (msg.startswith("용어집:") or msg.startswith("주입한 용어집 컨텍스트")
                or "용어집 컨텍스트 주입 활성화됨" in msg or "주입할 용어집 컨텍스트" in msg)
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER and per_chunk(r.getMessage())]


@pytest.mark.parametrize("run", [_standard, _integrity], ids=["standard", "integrity"])
def test_summary_line_is_info_and_details_are_debug(run, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    run(_service(), "勇者と魔王が来た")

    records = _glossary_records(caplog)
    info = [m for level, m in records if level == logging.INFO]
    debug = [m for level, m in records if level == logging.DEBUG]
    assert info == ["용어집: 1개 주입 (키워드 2, 의미 0, 상한으로 1개 제외)"]
    assert any("勇者 -> 용사" in m for m in debug)
    assert not any("勇者" in m for m in info)


@pytest.mark.parametrize("run", [_standard, _integrity], ids=["standard", "integrity"])
@pytest.mark.parametrize("overrides, text, expected", [
    ({}, "誰も来ない", "용어집: 0개 (청크에 해당 용어 없음)"),
    ({"enable_dynamic_glossary_injection": False}, "勇者が来た", "용어집: 0개 (동적 주입 꺼짐)"),
], ids=["no-match", "disabled"])
def test_zero_injection_still_logs_reason(run, overrides, text, expected, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    run(_service(**overrides), text)

    assert [m for _, m in _glossary_records(caplog)] == [expected]
