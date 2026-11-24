import asyncio
import json
import zipfile
from unittest.mock import MagicMock

import pytest

from core.exceptions import BtgApiAuthenticationException, BtgTranslationException
from domain.translation_service import TranslationService


def _service(workers=2, items=1):
    return TranslationService(
        gemini_client=MagicMock(),
        config={"max_workers": workers, "integrity_max_items": items},
    )


def _write_epub(path, chapters):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("style.css", "html { writing-mode: vertical-rl; }")
        archive.writestr("book.opf", '<spine page-progression-direction="rtl"/>')
        for name, paragraphs in chapters.items():
            content = "".join(f"<p>{text}</p>" for text in paragraphs)
            archive.writestr(name, f"<html><body>{content}</body></html>")


def _translated(chunk):
    return {unit.id: f"번역:{unit.text}" for unit in chunk}


@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [1, 2])
async def test_integrity_workers_keep_output_order_and_monotonic_progress(tmp_path, monkeypatch, workers):
    service = _service(workers)
    progress = []
    committed = []
    started = []
    second_finished = asyncio.Event()
    active = peak = 0

    async def translate(chunk, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        text = chunk[0].text
        started.append(text)
        try:
            if workers == 2 and text == "first":
                await asyncio.wait_for(second_finished.wait(), timeout=2)
            return _translated(chunk)
        finally:
            active -= 1

    def on_commit(index, chunk, result):
        committed.append(index)
        if index == 1:
            second_finished.set()

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    destination = tmp_path / "result.txt"
    result = await service.translate_text_integrity(
        "first\nsecond\nthird", destination,
        progress_callback=progress.append, on_chunk_translated=on_commit,
    )

    assert peak == workers
    assert started == ["first", "second", "third"]
    assert committed[0] == (1 if workers == 2 else 0)
    assert sorted(committed) == [0, 1, 2]
    assert [dto.processed_chunks for dto in progress] == [0, 1, 2, 3]
    assert progress[1].completed_chunk_indices == [committed[0]]
    assert result.splitlines() == ["번역:first", "번역:second", "번역:third"]
    assert destination.read_text(encoding="utf-8") == result


@pytest.mark.asyncio
async def test_integrity_workers_restore_completed_and_partial_chunks(tmp_path, monkeypatch):
    service = _service(items=2)
    destination = tmp_path / "result.txt"
    cache = service.integrity_temp_dir_for(destination)
    cache.mkdir()
    text = "one\ntwo\nthree\nfour\nfive"
    completed, partial, _ = service.split_integrity_chunks(text)
    service.integrity_chunk_file(cache, 0).write_text(json.dumps(_translated(completed)), encoding="utf-8")
    service.integrity_partial_file(cache, 1).write_text(json.dumps(_translated(partial[:1])), encoding="utf-8")
    calls = {}

    async def translate(chunk, **kwargs):
        calls[tuple(unit.text for unit in chunk)] = kwargs.get("retry_depth", 0)
        return _translated(chunk)

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    result = await service.translate_text_integrity(text, destination)

    assert calls == {("four",): 1, ("five",): 0}
    assert result.splitlines() == [f"번역:{line}" for line in text.splitlines()]
    assert not service.integrity_partial_file(cache, 1).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["integrity", "epub"])
async def test_fatal_failure_cancels_active_worker_before_next_chunk(tmp_path, monkeypatch, mode):
    service = _service()
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()
    started = []
    output = tmp_path / f"out.{mode}"

    async def translate(chunk, **kwargs):
        text = chunk[0].text
        started.append(text)
        if text == "first":
            await asyncio.wait_for(second_started.wait(), timeout=2)
            raise BtgApiAuthenticationException("인증 실패")
        second_started.set()
        try:
            await asyncio.Future()
        finally:
            second_cancelled.set()

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    if mode == "integrity":
        with pytest.raises(BtgApiAuthenticationException):
            await service.translate_text_integrity("first\nsecond\nthird", output)
    else:
        source = tmp_path / "input.epub"
        _write_epub(source, {"one.xhtml": ["first"], "two.xhtml": ["second"], "three.xhtml": ["third"]})
        with pytest.raises(BtgTranslationException) as error:
            await service.translate_epub(source, output)
        assert isinstance(error.value.__cause__, BtgApiAuthenticationException)
    assert started == ["first", "second"]
    assert second_cancelled.is_set()
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("across_chapters", [False, True])
async def test_epub_workers_are_limited_per_file_and_preserve_entries(tmp_path, monkeypatch, workers, across_chapters):
    service = _service(workers)
    source = tmp_path / "in.epub"
    output = tmp_path / "out.epub"
    chapters = {"chapter.xhtml": ["first", "second", "third"]}
    if across_chapters:
        chapters = {"a/b.xhtml": ["first"], "a_b.xhtml": ["second"], "c.xhtml": ["third"]}
    _write_epub(source, chapters)

    active = peak = 0
    started = []
    second_started = asyncio.Event()

    async def translate(chunk, **kwargs):
        nonlocal active, peak
        text = chunk[0].text
        active += 1
        peak = max(peak, active)
        started.append(text)
        try:
            if text == "second":
                second_started.set()
            if workers == 2 and text == "first":
                await asyncio.wait_for(second_started.wait(), timeout=2)
            return _translated(chunk)
        finally:
            active -= 1

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    await service.translate_epub(source, output)

    assert peak == workers
    assert started == ["first", "second", "third"]
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["mimetype", "style.css", "book.opf", *chapters]
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert b"horizontal-tb" in archive.read("style.css")
        assert b'page-progression-direction="ltr"' in archive.read("book.opf")
        for name, paragraphs in chapters.items():
            chapter = archive.read(name).decode("utf-8")
            for paragraph in paragraphs:
                assert f"번역:{paragraph}" in chapter


@pytest.mark.asyncio
async def test_epub_cancellation_keeps_completed_chapters_for_resume(tmp_path, monkeypatch):
    service = _service()
    source = tmp_path / "in.epub"
    output = tmp_path / "out.epub"
    output.write_bytes(b"previous output")
    _write_epub(source, {"a.xhtml": ["first"], "b.xhtml": ["second"], "c.xhtml": ["third"]})

    calls = []
    pending_started = asyncio.Event()

    async def translate(chunk, **kwargs):
        text = chunk[0].text
        calls.append(text)
        if text != "first":
            pending_started.set()
            await asyncio.Future()
        return _translated(chunk)

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    task = asyncio.create_task(service.translate_epub(source, output))
    await asyncio.wait_for(pending_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert output.read_bytes() == b"previous output"
    cache = service.epub_temp_dir_for(source)
    assert json.loads((cache / "epub_metadata.json").read_text(encoding="utf-8")) == {
        "translated_files": ["a.xhtml"],
    }

    calls.clear()

    async def resume(chunk, **kwargs):
        calls.append(chunk[0].text)
        return _translated(chunk)

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", resume)
    await service.translate_epub(source, output)
    assert calls == ["second", "third"]
    with zipfile.ZipFile(output) as archive:
        assert "번역:first" in archive.read("a.xhtml").decode("utf-8")
    assert not cache.exists()


@pytest.mark.asyncio
async def test_epub_preserves_failed_chapter_and_translates_next(tmp_path, monkeypatch):
    service = _service()
    source = tmp_path / "in.epub"
    output = tmp_path / "out.epub"
    _write_epub(source, {"a.xhtml": ["first", "second"], "b.xhtml": ["third"]})
    calls = []

    async def translate(chunk, **kwargs):
        text = chunk[0].text
        calls.append(text)
        if text == "first":
            raise ValueError("chapter failed")
        return _translated(chunk)

    monkeypatch.setattr(service, "_translate_chunk_waiting_out_overload", translate)
    await service.translate_epub(source, output)

    assert calls == ["first", "third"]
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output) as archive:
        assert archive.read("a.xhtml") == original.read("a.xhtml")
        assert "번역:third" in archive.read("b.xhtml").decode("utf-8")
