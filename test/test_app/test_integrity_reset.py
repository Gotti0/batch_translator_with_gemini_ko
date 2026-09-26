"""
검토 탭 '초기화'가 무결성 파이프라인에서 실제 재번역으로 이어지는지 확인한다.

무결성 모드는 이어하기를 임시 폴더의 chunk_<i>.json으로 판단하므로, 메타데이터만 지우면
다음 실행이 청크를 건너뛰고 진행 콜백이 기록을 되살렸다. (Gemini 호출은 가짜로 바꾼다.)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.app_service import AppService
from domain.review_providers.factory import get_review_provider
from domain.review_providers.integrity_provider import IntegrityReviewProvider
from infrastructure.file_handler import load_metadata
from infrastructure.gemini_client import GeminiClient

LINES = ["一行目", "二行目", "三行目", "四行目"]


def _fake(tag, fail_on=None):
    def translate(prompt, **kwargs):
        text = prompt[-1].parts[-1].text
        units, _ = json.JSONDecoder().raw_decode(text[text.index("[{"):])
        if fail_on and any(u["text"] == fail_on for u in units):
            raise RuntimeError("테스트용 실패")
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


async def _run(app, workspace, side_effect):
    with patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=side_effect)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "novel_translated.txt")
    return gen


def _reset(app, workspace, indices):
    src = str(workspace / "novel.txt")
    provider = get_review_provider(src, app)
    assert isinstance(provider, IntegrityReviewProvider)
    provider.reset_chunks(src, load_metadata(src), indices)


@pytest.mark.asyncio
async def test_reset_chunk_is_retranslated(workspace):
    app = AppService(workspace / "config.json")
    await _run(app, workspace, _fake("OLD"))
    temp_dir = workspace / "novel_translated_integrity_temp"
    assert sorted(p.name for p in temp_dir.glob("chunk_*.json")) == [f"chunk_{i}.json" for i in range(4)]

    _reset(app, workspace, [1])
    assert not (temp_dir / "chunk_1.json").exists()
    meta = load_metadata(workspace / "novel.txt")
    assert "1" not in meta["translated_chunks"] and meta["status"] == "in_progress"

    gen = await _run(app, workspace, _fake("NEW"))
    assert gen.await_count == 1  # 초기화한 청크만 다시 번역한다
    out = (workspace / "novel_translated.txt").read_text(encoding="utf-8").splitlines()
    assert out == ["[OLD]一行目", "[NEW]二行目", "[OLD]三行目", "[OLD]四行目"]
    meta = load_metadata(workspace / "novel.txt")
    assert sorted(meta["translated_chunks"]) == ["0", "1", "2", "3"] and meta["status"] == "completed"


@pytest.mark.asyncio
async def test_metadata_follows_completed_indices_when_gap_remains(workspace):
    """중간 청크가 비어 있는 채 중단되면 그 번호는 성공으로 찍히지 않는다 (개수로 0..N-1을 채우지 않는다)"""
    app = AppService(workspace / "config.json")
    await _run(app, workspace, _fake("OLD"))
    _reset(app, workspace, [1])

    with pytest.raises(Exception):
        await _run(app, workspace, _fake("NEW", fail_on="二行目"))

    meta = load_metadata(workspace / "novel.txt")
    assert sorted(meta["translated_chunks"]) == ["0", "2", "3"]


def test_standard_provider_reset_only_touches_metadata(tmp_path):
    """표준 파이프라인은 메타데이터로 이어하기를 판단하므로 기본 구현은 메타데이터만 지운다"""
    src = tmp_path / "a.txt"
    src.write_text("本文", encoding="utf-8")
    meta = {"pipeline_type": "standard", "total_chunks": 2, "status": "completed",
            "translated_chunks": {"0": {}, "1": {}}, "failed_chunks": {"1": "x"}}
    app = MagicMock()
    app.config = {}
    provider = get_review_provider(str(src), app)
    provider.reset_chunks(str(src), meta, [1])

    saved = load_metadata(src)
    assert saved["translated_chunks"] == {"0": {}} and saved["failed_chunks"] == {}
    assert saved["status"] == "in_progress"
