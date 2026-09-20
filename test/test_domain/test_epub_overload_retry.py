"""EPUB 번역도 과부하(503)가 풀릴 때까지 청크를 다시 시도하는지 확인한다.

예전에는 챕터 단위 포괄 except가 503을 삼켜 챕터 전체가 원본으로 되돌아갔다. 앞서 번역한
청크까지 버려지고, 과부하가 이어지면 모든 챕터를 헛돌았다. 지금은 무결성 번역·용어집 추출과
같은 방침으로 청크가 성공할 때까지 기다린다.
"""
import asyncio
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.translation_service import TranslationService
from infrastructure.gemini_client import GeminiServiceUnavailableException

CHAPTER = "OEBPS/chapter1.xhtml"


@pytest.fixture
def epub_input(tmp_path):
    """챕터 하나짜리 최소 EPUB. 파이프라인은 mimetype과 .xhtml 항목만 본다."""
    path = tmp_path / "sample_input.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            CHAPTER,
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<p>First paragraph.</p><p>Second paragraph.</p>"
            "</body></html>",
        )
    return path


def _service(side_effect):
    client = MagicMock()
    client.generate_text_async = AsyncMock(side_effect=side_effect)
    return TranslationService(
        gemini_client=client,
        config={
            "model_name": "gemini-test",
            "min_content_safety_chunk_size": 0,
            "integrity_max_items": 200,
        },
    )


def _responder(fail_times):
    """앞의 fail_times회는 과부하를 내고 그 뒤로는 번역한다."""
    calls = []

    async def respond(**kwargs):
        text = kwargs["prompt"][0].parts[0].text
        calls.append(text)
        if len(calls) <= fail_times:
            raise GeminiServiceUnavailableException("모델 과부하(503)로 2회 시도했으나 실패")
        start, end = text.find("["), text.rfind("]") + 1
        items = json.loads(text[start:end])
        return [{"id": item["id"], "translated_text": f"번역:{item['text']}"} for item in items]

    return respond, calls


def test_overloaded_chapter_chunk_is_retried_until_it_succeeds(epub_input, tmp_path):
    """과부하가 풀리면 챕터가 번역된다. 원본으로 되돌아가지 않는다."""
    respond, calls = _responder(fail_times=2)
    service = _service(respond)
    output = tmp_path / "out.epub"

    asyncio.run(service.translate_epub(epub_input, output))

    with zipfile.ZipFile(output, "r") as z:
        chapter = z.read(CHAPTER).decode("utf-8")

    assert "번역:First paragraph." in chapter
    assert "번역:Second paragraph." in chapter
    # 실패 2회 + 성공 1회.
    assert len(calls) == 3


def test_stop_request_breaks_out_of_the_epub_retry_loop(epub_input, tmp_path):
    """상한이 없으므로 중단 요청이 유일한 탈출구다."""
    respond, calls = _responder(fail_times=99)
    service = _service(respond)
    output = tmp_path / "out.epub"

    attempts = {"n": 0}

    def stop_check():
        attempts["n"] += 1
        return attempts["n"] > 4

    service.set_stop_check_callback(stop_check)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.translate_epub(epub_input, output))

    # 무한히 돌지 않고 중단 시점에서 멈췄다.
    assert len(calls) < 6
