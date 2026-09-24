"""
번역 장기기억 통합 테스트: AppService 표준 번역에서 뒤 청크가 앞 청크의 번역을 예시로 받는지 확인한다.
(Gemini 호출과 임베딩은 가짜로 바꾼다.)
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "test_domain"))
from test_translation_memory import FakeEmbedder  # noqa: E402

from app.app_service import AppService
from infrastructure.gemini_client import GeminiClient

LINES = [
    "勇者リリアは剣を抜いた。「待っていろ、魔王。必ずお前を倒す」と彼女は叫んだ。",
    "夜の森は静かだった。月が雲に隠れ、冷たい風が木々の間を吹き抜けていく。",
    "朝の市場はにぎやかだった。焼きたてのパンの匂いが通りいっぱいに広がっている。",
    "勇者リリアは剣を構えた。「逃がさないぞ、魔王。今度こそお前を倒す」と彼女は叫んだ。",
]


def _fake_translate(prompt, **kwargs):
    src = prompt[-1].parts[-1].text.split("Translate: ", 1)[-1]
    return "\n".join("[KO]" + line for line in src.split("\n"))


@pytest.fixture
def workspace(tmp_path):
    inp = tmp_path / "novel.txt"
    inp.write_text("\n\n".join(LINES), encoding="utf-8")
    cfg = {
        "llm_provider": "gemini", "api_keys": ["k"], "model_name": "gemini-3.8-flash",
        "translation_mode": "standard", "chunk_size": 500, "max_workers": 1,
        "prompts": "Translate: {{slot}}", "enable_pagefold": False, "enable_prefill_translation": False,
        "enable_dynamic_glossary_injection": False, "enable_post_processing": False,
        "use_content_safety_retry": False,
        "enable_translation_memory": True, "voyage_api_key": "vk", "memory_top_k": 1, "memory_min_similarity": 0.3,
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _chunks(app, path):
    return app.chunk_service.create_chunks_from_file_content(path.read_text(encoding="utf-8"), 500)


@pytest.mark.asyncio
async def test_later_chunk_gets_earlier_translation_as_example(workspace):
    # 청크 크기를 문단 하나로 맞추기 위해 ChunkService 결과를 문단 단위로 고정한다
    app = AppService(workspace / "config.json")
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")

    calls = gen.call_args_list
    assert len(calls) == 4
    first_sys = calls[0].kwargs.get("system_instruction_text") or ""
    assert "<translation_memory>" not in first_sys  # 첫 청크는 기억이 비어 있다

    last_sys = calls[3].kwargs.get("system_instruction_text") or ""
    assert "<translation_memory>" in last_sys
    assert "[KO]" + LINES[0] in last_sys  # 가장 비슷한 청크 0의 번역이 예시로 들어간다

    memory = app.translation_service.translation_memory
    assert memory.summary()["translated"] == 4
    assert (workspace / "novel_memory" / "items.jsonl").exists()
    assert "[KO]" + LINES[3] in (workspace / "out.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_placeholder_puts_memory_in_prompt(workspace):
    cfg = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    cfg["prompts"] = "{{translation_memory}}\nTranslate: {{slot}}"
    (workspace / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    app = AppService(workspace / "config.json")
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")

    last = gen.call_args_list[3]
    user_text = last.kwargs["prompt"][-1].parts[-1].text
    assert user_text.startswith("<translation_memory>")
    assert "<translation_memory>" not in (last.kwargs.get("system_instruction_text") or "")
    first_text = gen.call_args_list[0].kwargs["prompt"][-1].parts[-1].text
    assert first_text.startswith("\nTranslate:")  # 기억이 없으면 자리표시자는 빈 문자열


@pytest.mark.asyncio
async def test_resume_backfills_memory_and_skips_reembedding(workspace):
    app = AppService(workspace / "config.json")
    emb = FakeEmbedder()
    app.embedding_client_factory = lambda cfg: emb
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)):
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")
    embedded_first = sum(len(c) for c in emb.calls)

    # 기억 폴더를 지우고 다시 준비하면 청크 백업 파일에서 번역을 되살린다
    import shutil
    shutil.rmtree(workspace / "novel_memory")
    await app._prepare_translation_memory_async(workspace / "novel.txt", list(LINES))
    assert app.translation_service.translation_memory.summary()["translated"] == 4

    # 같은 원문으로 다시 준비하면 새로 임베딩하지 않는다
    before = sum(len(c) for c in emb.calls)
    await app._prepare_translation_memory_async(workspace / "novel.txt", list(LINES))
    assert sum(len(c) for c in emb.calls) == before
    assert embedded_first == 4


@pytest.mark.asyncio
async def test_embedding_failure_does_not_block_translation(workspace):
    app = AppService(workspace / "config.json")

    def broken(cfg):
        raise RuntimeError("voyage down")

    app.embedding_client_factory = broken
    statuses = []
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt", status_callback=statuses.append)
    assert gen.await_count == 4
    assert app.translation_service.translation_memory is None
    assert any("번역 기억 준비 실패" in s for s in statuses)


@pytest.mark.asyncio
async def test_disabled_by_default_changes_nothing(workspace):
    cfg = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    cfg["enable_translation_memory"] = False
    (workspace / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    app = AppService(workspace / "config.json")
    app.embedding_client_factory = lambda cfg: pytest.fail("임베딩을 호출하면 안 된다")
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")
    assert all("<translation_memory>" not in (c.kwargs.get("system_instruction_text") or "") for c in gen.call_args_list)
    assert not (workspace / "novel_memory").exists()


@pytest.mark.asyncio
async def test_character_notes_extracted_and_recalled_later(workspace):
    """투 트랙 추출: 청크 0에서 뽑힌 인물 메모가, 그 인물이 다시 나온 뒤 청크에 주입된다"""
    from domain.memory_extractor import ExtractedEntity

    cfg = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    cfg["enable_memory_extraction"] = True
    (workspace / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    class FakeExtractor:
        calls = []

        async def extract(self, source, translation, known):
            self.calls.append(source)
            if "リリア" in source and not known:
                return [ExtractedEntity(name="リリア", translated_name="릴리아", note="반말, 마왕을 '너'라 부름")]
            return []

    app = AppService(workspace / "config.json")
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    extractor = FakeExtractor()
    app.memory_extractor_factory = lambda cfg: extractor
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")

    assert len(extractor.calls) == 4  # 청크마다 한 번, 뒤에서
    last_sys = gen.call_args_list[3].kwargs.get("system_instruction_text") or ""
    assert '<entity name="リリア" ko="릴리아"' in last_sys and "반말" in last_sys
    graph = app.translation_service.translation_memory.graph
    unit = graph.units["ent:リリア"]
    assert unit.evidence and "リリア" in unit.evidence[0]["excerpt"]
    # 다시 열어도 그래프가 남아 있다
    from domain.translation_memory import TranslationMemoryStore
    reopened = TranslationMemoryStore.open(workspace / "novel.txt", "fake-1", 64)
    assert "ent:リリア" in reopened.graph.units


@pytest.mark.asyncio
async def test_extraction_failure_does_not_block(workspace):
    cfg = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    cfg["enable_memory_extraction"] = True
    (workspace / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    class Broken:
        async def extract(self, *a):
            raise RuntimeError("extract down")

    app = AppService(workspace / "config.json")
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    app.memory_extractor_factory = lambda cfg: Broken()
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)) as gen:
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")
    assert gen.await_count == 4
    assert (workspace / "out.txt").exists()


@pytest.mark.asyncio
async def test_memory_overview(workspace):
    from domain.memory_extractor import ExtractedEntity
    cfg = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    cfg["enable_memory_extraction"] = True
    (workspace / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    class Ex:
        async def extract(self, source, translation, known):
            return [ExtractedEntity(name="リリア", translated_name="릴리아", note="반말")] if "リリア" in source else []

    app = AppService(workspace / "config.json")
    assert app.get_memory_overview(workspace / "novel.txt") is None
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    app.memory_extractor_factory = lambda cfg: Ex()
    with patch.object(app.chunk_service, "create_chunks_from_file_content", return_value=list(LINES)), \
         patch.object(GeminiClient, "generate_text_async", new=AsyncMock(side_effect=_fake_translate)):
        await app.start_translation_async(workspace / "novel.txt", workspace / "out.txt")
    ov = app.get_memory_overview(workspace / "novel.txt")
    assert ov["summary"]["entity"] == 1 and ov["summary"]["episode"] == 4
    assert ov["entities"][0]["name"] == "リリア" and ov["entities"][0]["fire_count"] >= 1
