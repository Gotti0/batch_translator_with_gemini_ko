import io
import logging
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.app_service import AppService
from core.dtos import TranslationJobProgressDTO
from domain.translation_service import TranslationService
from gui_qt.tabs_qt.log_tab_qt import QtGuiLogHandler, TqdmToQt, _QtLogEmitter


def _write_epub(path: Path, chapters: dict[str, list[str]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, paragraphs in chapters.items():
            body = "".join(f"<p>{text}</p>" for text in paragraphs)
            archive.writestr(name, f"<html><body>{body}</body></html>")


def _make_service(workers: int = 2) -> TranslationService:
    return TranslationService(
        MagicMock(),
        {
            "max_workers": workers,
            "chunk_size": 20,
            "integrity_max_items": 1,
            "enable_dynamic_glossary_injection": False,
        },
    )


@pytest.mark.asyncio
async def test_epub_translation_emits_gui_logs_tqdm_and_progress(tmp_path, monkeypatch):
    service = _make_service(workers=2)
    source = tmp_path / "book.epub"
    output = tmp_path / "book_translated.epub"
    _write_epub(source, {"OEBPS/c1.xhtml": ["first", "second"], "OEBPS/c2.xhtml": ["third"]})

    async def fake_translate(chunk, **kwargs):
        return {unit.id: f"번역:{unit.text}" for unit in chunk}

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", fake_translate)

    emitter = _QtLogEmitter()
    gui_messages: list[tuple[str, str]] = []
    emitter.message.connect(lambda msg, level: gui_messages.append((msg, level)))

    handler = QtGuiLogHandler(emitter)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S"))
    root_logger = logging.getLogger()
    service_logger = logging.getLogger("domain.translation_service")
    prev_level = service_logger.level
    service_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    tqdm_stream = TqdmToQt(emitter)

    progress_events: list[TranslationJobProgressDTO] = []
    status_events: list[str] = []

    try:
        await service.translate_epub(
            source,
            output,
            progress_callback=progress_events.append,
            status_callback=status_events.append,
            tqdm_file_stream=tqdm_stream,
        )
    finally:
        root_logger.removeHandler(handler)
        service_logger.setLevel(prev_level)

    info_texts = [msg for msg, level in gui_messages if level == "INFO"]
    tqdm_texts = [msg for msg, level in gui_messages if level == "TQDM"]

    assert any("🚀 EPUB 번역 시작: book.epub" in msg for msg in info_texts)
    assert any("📄 챕터 처리 중: OEBPS/c1.xhtml" in msg for msg in info_texts)
    assert any("📄 챕터 처리 중: OEBPS/c2.xhtml" in msg for msg in info_texts)
    assert any("📦 EPUB 챕터 OEBPS/c1.xhtml 청크 1/2 번역 중" in msg for msg in info_texts)
    assert any("📦 EPUB 챕터 OEBPS/c1.xhtml 청크 2/2 번역 중" in msg for msg in info_texts)
    assert any("📦 EPUB 챕터 OEBPS/c2.xhtml 청크 1/1 번역 중" in msg for msg in info_texts)
    assert any("✅ EPUB 챕터 완료: OEBPS/c1.xhtml" in msg for msg in info_texts)
    assert any("✅ EPUB 챕터 완료: OEBPS/c2.xhtml" in msg for msg in info_texts)
    assert any("✅ EPUB 번역 완료: book_translated.epub" in msg for msg in info_texts)

    assert any("EPUB 번역:" in msg for msg in tqdm_texts)
    assert any("2/2" in msg for msg in tqdm_texts)

    assert progress_events[0].total_chunks == 2
    assert progress_events[0].processed_chunks == 0
    assert progress_events[-1].processed_chunks == 2
    assert progress_events[-1].successful_chunks == 2
    assert progress_events[-1].failed_chunks == 0
    assert any("EPUB 챕터 OEBPS/c1.xhtml 청크 1/2 번역 중..." == s for s in status_events)
    assert any("EPUB 챕터 완료: OEBPS/c2.xhtml" == s for s in status_events)


@pytest.mark.asyncio
async def test_epub_progress_tracks_empty_and_failed_chapters(tmp_path, monkeypatch):
    service = _make_service(workers=1)
    source = tmp_path / "mixed.epub"
    output = tmp_path / "mixed_translated.epub"
    _write_epub(
        source,
        {
            "cover.xhtml": [],
            "bad.xhtml": ["boom"],
            "good.xhtml": ["ok"],
        },
    )

    async def fake_translate(chunk, **kwargs):
        if chunk[0].text == "boom":
            raise ValueError("chapter boom")
        return {unit.id: f"번역:{unit.text}" for unit in chunk}

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", fake_translate)

    progress_events: list[TranslationJobProgressDTO] = []
    tqdm_buf = io.StringIO()

    await service.translate_epub(
        source,
        output,
        progress_callback=progress_events.append,
        tqdm_file_stream=tqdm_buf,
    )

    assert progress_events[0].total_chunks == 3
    assert progress_events[-1].processed_chunks == 3
    assert progress_events[-1].successful_chunks == 2
    assert progress_events[-1].failed_chunks == 1
    assert "3/3" in tqdm_buf.getvalue()


@pytest.mark.asyncio
async def test_app_service_forwards_progress_and_tqdm_to_translate_epub(tmp_path, monkeypatch):
    app_service = object.__new__(AppService)
    app_service.config = {"translation_mode": "epub"}
    app_service.translation_service = MagicMock()
    app_service.translation_service.translate_epub = AsyncMock()
    app_service.chunk_service = MagicMock()
    app_service.processed_chunks_count = 0
    app_service.successful_chunks_count = 0
    app_service.failed_chunks_count = 0

    monkeypatch.setattr("app.app_service.save_metadata", lambda *args, **kwargs: None)

    progress_cb = MagicMock()
    status_cb = MagicMock()
    tqdm_stream = io.StringIO()

    source = tmp_path / "novel.epub"
    output = tmp_path / "novel_translated.epub"

    await AppService._do_translation_async(
        app_service,
        str(source),
        str(output),
        progress_callback=progress_cb,
        status_callback=status_cb,
        tqdm_file_stream=tqdm_stream,
        translation_mode_override="epub",
    )

    app_service.translation_service.translate_epub.assert_awaited_once()
    args, kwargs = app_service.translation_service.translate_epub.call_args
    assert args == (str(source), str(output))
    assert callable(kwargs["progress_callback"])
    assert kwargs["status_callback"] is status_cb
    assert kwargs["tqdm_file_stream"] is tqdm_stream


def test_ollama_provider_does_not_warn_missing_gemini_api_key(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"llm_provider": "ollama", "ollama_base_url": "http://localhost:11434", "model_name": "gemma3:12b"}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.app_service.LLMClientFactory.create_client", lambda **kwargs: MagicMock())

    with caplog.at_level(logging.WARNING, logger="app.app_service"):
        AppService(config_file_path=config_path)

    assert not any("API 키가 설정되지 않음" in record.message for record in caplog.records)


def test_gemini_provider_warns_when_api_key_missing(tmp_path, caplog):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"llm_provider": "gemini", "use_vertex_ai": false}', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.app_service"):
        AppService(config_file_path=config_path)

    assert any("API 키가 설정되지 않음" in record.message for record in caplog.records)
