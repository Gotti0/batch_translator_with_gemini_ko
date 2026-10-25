"""
새로 번역(--force-new)이 무결성·EPUB 진행 상태까지 지우는지 확인한다.

무결성 모드는 임시 폴더의 chunk_<i>.json으로 이어하기를 판단하므로, 예전 CLI처럼 메타데이터와
출력 파일만 지우면 다음 실행이 모든 청크를 건너뛰고 이전 결과를 그대로 썼다. (Gemini 호출은 가짜로 바꾼다.)
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.app_service import AppService
from domain.translation_service import TranslationService
from infrastructure.file_handler import delete_file, get_metadata_file_path
from infrastructure.gemini_client import GeminiClient

LINES = ["一行目", "二行目", "三行目", "四行目"]


def _fake(tag):
    def translate(prompt, **kwargs):
        text = prompt[-1].parts[-1].text
        units, _ = json.JSONDecoder().raw_decode(text[text.index("[{"):])
        return [{"id": u["id"], "translated_text": f"[{tag}]{u['text']}"} for u in units]
    return translate


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "novel.txt").write_text("\n".join(LINES), encoding="utf-8")
    cfg = {
        "llm_provider": "gemini", "api_keys": ["k"], "model_name": "gemini-3.8-flash",
        "translation_mode": "integrity", "integrity_max_items": 1, "chunk_size": 500, "max_workers": 1,
        "prompts": "Translate: {{slot}}", "enable_pagefold": False, "enable_prefill_translation": False,
        "enable_dynamic_glossary_injection": False, "enable_post_processing": False,
        "use_content_safety_retry": False, "enable_translation_memory": False,
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return tmp_path


async def _run(app, workspace, tag):
    with patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake(tag))) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "novel_translated.txt")
    return gen


@pytest.mark.asyncio
async def test_metadata_and_output_deletion_alone_reuses_integrity_progress(workspace):
    """고치기 전 CLI가 하던 삭제만으로는 무결성 모드가 새로 번역하지 않는다 (진행 폴더가 근거이기 때문)"""
    app = AppService(workspace / "config.json")
    await _run(app, workspace, "OLD")

    delete_file(get_metadata_file_path(workspace / "novel.txt"))
    delete_file(workspace / "novel_translated.txt")
    gen = await _run(app, workspace, "NEW")

    assert gen.await_count == 0
    assert (workspace / "novel_translated.txt").read_text(encoding="utf-8").splitlines()[0] == "[OLD]一行目"


@pytest.mark.asyncio
async def test_discard_saved_progress_retranslates_every_integrity_chunk(workspace):
    app = AppService(workspace / "config.json")
    await _run(app, workspace, "OLD")
    temp_dir = TranslationService.integrity_temp_dir_for(workspace / "novel_translated.txt")
    assert temp_dir.exists()

    app.discard_saved_progress(workspace / "novel.txt", workspace / "novel_translated.txt")
    assert not temp_dir.exists()
    assert not get_metadata_file_path(workspace / "novel.txt").exists()
    assert not (workspace / "novel_translated.txt").exists()

    gen = await _run(app, workspace, "NEW")
    assert gen.await_count == len(LINES)  # integrity_max_items=1이라 줄마다 한 청크
    out = (workspace / "novel_translated.txt").read_text(encoding="utf-8").splitlines()
    assert out == [f"[NEW]{line}" for line in LINES]


def test_discard_saved_progress_removes_epub_progress(workspace):
    epub = workspace / "book.epub"
    epub.write_bytes(b"")
    temp_dir = TranslationService.epub_temp_dir_for(epub)
    temp_dir.mkdir()
    (temp_dir / "epub_metadata.json").write_text(json.dumps({"translated_files": ["c1.xhtml"]}), encoding="utf-8")
    memory_dir = workspace / "book_memory"
    memory_dir.mkdir()

    AppService(workspace / "config.json").discard_saved_progress(epub, workspace / "book_translated.epub")

    assert not temp_dir.exists()
    # 번역 기억은 일관성용이라 새로 번역해도 남긴다
    assert memory_dir.exists()


def test_discard_saved_progress_without_previous_run_is_noop(workspace):
    AppService(workspace / "config.json").discard_saved_progress(workspace / "novel.txt", workspace / "none.txt")
    assert (workspace / "novel.txt").exists()
