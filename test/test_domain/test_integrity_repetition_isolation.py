"""
반복 의성어 등으로 인한 모델 퇴화 루프(BtgApiRepetitionException) 발생 시
무결성 청크 및 EPUB 번역에서 문제 줄만 원문으로 격리 보존하고
나머지 줄은 정상 번역되는지 검증하는 단위/통합 테스트.
"""

import asyncio
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dtos import TranslationUnit
from core.exceptions import BtgApiRepetitionException
from domain.translation_service import TranslationService
from infrastructure.gemini_client import GeminiServiceUnavailableException

CHAPTER = "OEBPS/chapter1.xhtml"


@pytest.fixture
def epub_with_onomatopoeia(tmp_path):
    """반복 의성어 문장이 포함된 테스트용 EPUB."""
    path = tmp_path / "sample_onomatopoeia.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            CHAPTER,
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<p>男は剣を構えた。</p>"
            "<p>『グガガァァァァァァァァッッッッ!!』</p>"
            "<p>魔獣の咆哮が森に響き渡る。</p>"
            "</body></html>",
        )
    return path


def _make_service(mock_generate_fn, config_overrides=None):
    client = MagicMock()
    client.generate_text_async = AsyncMock(side_effect=mock_generate_fn)
    cfg = {
        "model_name": "qwen3.5:9b",
        "min_content_safety_chunk_size": 0,
        "integrity_max_items": 200,
        "enable_pagefold": False,
    }
    if config_overrides:
        cfg.update(config_overrides)
    return TranslationService(gemini_client=client, config=cfg)


def _extract_items_from_prompt_text(text: str):
    start = text.find("[")
    end = text.rfind("]") + 1
    if start != -1 and end > start:
        return json.loads(text[start:end])
    return []


@pytest.mark.asyncio
async def test_repetition_exception_isolates_suspicious_line_and_translates_rest():
    """청크에 반복 의성어가 포함되어 전체 호출이 실패하면, 의심 줄을 격리하여 나머지 줄을 번역하고 문제 줄만 원문 유지."""
    call_history = []

    async def mock_generate(**kwargs):
        prompt_contents = kwargs.get("prompt")
        text = prompt_contents[0].parts[0].text if prompt_contents else ""
        items = _extract_items_from_prompt_text(text)
        call_history.append(items)

        # 'ァァァァ'가 포함된 항목이 있으면 반복 중단 에러 발생
        has_repetition = any("ァァァァ" in item.get("text", "") for item in items)
        if has_repetition:
            raise BtgApiRepetitionException("Ollama가 반복 생성을 감지해 중단했습니다 (500): token repeat limit reached")

        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    service = _make_service(mock_generate)
    chunk = [
        TranslationUnit(id="0", text="男は剣を構えた。"),
        TranslationUnit(id="1", text="『グガガァァァァァァァァッッッッ!!』"),
        TranslationUnit(id="2", text="魔獣の咆哮が森に響き渡る。"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)

    # 0번, 2번은 정상 번역되고, 1번(의성어)만 원문 그대로 유지되어야 함
    assert result["0"] == "번역:男は剣を構えた。"
    assert result["1"] == "『グガガァァァァァァァァッッッッ!!』"
    assert result["2"] == "번역:魔獣の咆哮が森に響き渡る。"

    # 호출 단계 검증:
    # 1. 3개 전체 호출 -> 실패
    # 2. 일반 줄 2개([0, 2]) 묶음 호출 -> 성공
    # 3. 의심 줄 [1] 단독 호출 -> 실패 -> 단일 항목 원문 유지로 복구
    assert len(call_history) == 3
    assert len(call_history[0]) == 3
    assert len(call_history[1]) == 2
    assert len(call_history[2]) == 1


@pytest.mark.asyncio
async def test_suspicious_line_succeeds_on_isolated_single_request():
    """묶음에서는 실패했지만 단독 1줄 호출에서는 모델이 성공하는 경우 모든 줄이 번역됨."""
    async def mock_generate(**kwargs):
        prompt_contents = kwargs.get("prompt")
        text = prompt_contents[0].parts[0].text if prompt_contents else ""
        items = _extract_items_from_prompt_text(text)

        # 항목이 2개 이상이면서 반복 의성어가 섞여 있으면 실패, 단독(1개)이면 성공한다고 가정
        has_repetition = any("ァァァァ" in item.get("text", "") for item in items)
        if has_repetition and len(items) > 1:
            raise BtgApiRepetitionException("token repeat limit reached")

        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    service = _make_service(mock_generate)
    chunk = [
        TranslationUnit(id="0", text="男は剣を構えた。"),
        TranslationUnit(id="1", text="『グガガァァァァァァァァッッッッ!!』"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)
    assert result["0"] == "번역:男は剣を構えた。"
    assert result["1"] == "번역:『グガガァァァァァァァァッッッッ!!』"


@pytest.mark.asyncio
async def test_repetition_exception_binary_split_fallback_when_regex_does_not_catch():
    """정규식 패턴에 잡히지 않는 문장이지만 반복 중단 에러가 난 경우 이진 분할로 문제 줄만 원문 유지."""
    async def mock_generate(**kwargs):
        prompt_contents = kwargs.get("prompt")
        text = prompt_contents[0].parts[0].text if prompt_contents else ""
        items = _extract_items_from_prompt_text(text)

        # id=="bad"가 든 호출은 무조건 에러
        if any(item.get("id") == "bad" for item in items):
            raise BtgApiRepetitionException("token repeat limit reached")

        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    service = _make_service(mock_generate)
    chunk = [
        TranslationUnit(id="0", text="일반 문장 A"),
        TranslationUnit(id="bad", text="특이한 퇴화 유발 문장"),
        TranslationUnit(id="2", text="일반 문장 B"),
        TranslationUnit(id="3", text="일반 문장 C"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)
    assert result["0"] == "번역:일반 문장 A"
    assert result["bad"] == "특이한 퇴화 유발 문장"
    assert result["2"] == "번역:일반 문장 B"
    assert result["3"] == "번역:일반 문장 C"


@pytest.mark.asyncio
async def test_repetition_isolation_works_even_if_content_safety_retry_disabled():
    """use_content_safety_retry가 False여도 반복 생성 중단 격리는 정상 수행됨."""
    async def mock_generate(**kwargs):
        prompt_contents = kwargs.get("prompt")
        text = prompt_contents[0].parts[0].text if prompt_contents else ""
        items = _extract_items_from_prompt_text(text)
        if any("ァァァァ" in item.get("text", "") for item in items):
            raise BtgApiRepetitionException("token repeat limit reached")
        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    service = _make_service(mock_generate, config_overrides={"use_content_safety_retry": False})
    chunk = [
        TranslationUnit(id="0", text="남자는 검을 들었다."),
        TranslationUnit(id="1", text="『グガガァァァァァァァァッッッッ!!』"),
    ]

    result = await service._translate_integrity_chunk_with_retry(chunk)
    assert result["0"] == "번역:남자는 검을 들었다."
    assert result["1"] == "『グガガァァァァァァァァッッッッ!!』"


@pytest.mark.asyncio
async def test_epub_translation_preserves_only_repetition_line_not_entire_chapter(
    epub_with_onomatopoeia, tmp_path
):
    """EPUB 번역 시 반복 의성어 줄이 실패하더라도 챕터 전체가 일본어 원문으로 폐기되지 않고,
    정상 문장은 번역되고 해당 의성어 줄만 원문으로 보존되어 완성된 EPUB에 저장됨."""
    async def mock_generate(**kwargs):
        prompt_contents = kwargs.get("prompt")
        text = prompt_contents[0].parts[0].text if prompt_contents else ""
        items = _extract_items_from_prompt_text(text)
        if any("ァァァァ" in item.get("text", "") for item in items):
            raise BtgApiRepetitionException("token repeat limit reached")
        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    service = _make_service(mock_generate)
    output_epub = tmp_path / "output.epub"

    await service.translate_epub(epub_with_onomatopoeia, output_epub)

    with zipfile.ZipFile(output_epub, "r") as z:
        chapter_content = z.read(CHAPTER).decode("utf-8")

    # 정상 문장은 한국어 번역이 들어있고, 의성어 줄만 원문으로 보존되어 챕터가 완성되어야 함!
    assert "번역:男は剣を構えた。" in chapter_content
    assert "『グガガァァァァァァァァッッッッ!!』" in chapter_content
    assert "번역:魔獣の咆哮が森に響き渡る。" in chapter_content
