"""빈 번역 결과가 성공으로 취급되지 않는지 검증한다.

원래 이 파일은 `translate_text_with_content_safety_retry`가 빈 문자열을 돌려주도록 목을
심고, 메타데이터에 완료 기록이 남지 않는지를 보는 수동 스크립트였다. 비동기 마이그레이션
이후 유효하지 않다는 주석과 함께 비활성화를 시도했으나 본문 일부만 주석 처리돼
`IndentationError`로 수집되지 않았다.

방어선은 두 겹이다. 아래층 `translate_text_async`가 빈 응답과 None을 예외로 바꾸고,
메인 번역 루프가 내용 있는 청크의 빈 번역문을 실패로 다룬다. 둘 다 고정한다. 아래층만
있을 때는 그 방어가 뚫리면 빈 번역문이 완료로 기록되고 이어하기가 다시 집지 않아 결과물에
구멍이 남았다.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.app_service import AppService
from core.exceptions import BtgTranslationException
from domain.translation_service import TranslationService
from infrastructure.file_handler import get_metadata_file_path


def _service(return_value):
    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value=return_value)
    return TranslationService(
        gemini_client=client,
        config={"model_name": "gemini-test", "use_content_safety_retry": False},
    )


@pytest.mark.parametrize("empty_response", ["", "   ", "\n\n"])
def test_empty_translation_is_rejected(empty_response):
    """공백뿐인 응답은 번역 성공이 아니라 오류다."""
    service = _service(empty_response)

    with pytest.raises(BtgTranslationException) as excinfo:
        asyncio.run(service.translate_text_async("번역할 내용이 있는 원문"))

    assert "콘텐츠 안전 문제" in str(excinfo.value)


def test_none_response_is_rejected():
    """응답 자체가 없으면 빈 문자열을 반환하지 않고 오류로 끝낸다."""
    service = _service(None)

    with pytest.raises(BtgTranslationException):
        asyncio.run(service.translate_text_async("번역할 내용이 있는 원문"))


def test_empty_input_returns_empty_without_calling_api():
    """원문이 비어 있으면 그것은 오류가 아니라 빈 결과다. API를 부르지 않는다."""
    service = _service("무엇이든")

    result = asyncio.run(service.translate_chunk_async("   "))

    assert result == ""
    service.gemini_client.generate_text_async.assert_not_called()


def _run_translation(source_path, output_path, fake_translate):
    service = AppService()
    service.load_app_config(
        runtime_overrides={
            "chunk_size": 1000,
            "api_keys": ["dummy-key-for-init"],
            "max_workers": 1,
            "translation_mode": "standard",
        }
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", fake_translate)
        asyncio.run(service.start_translation_async(source_path, output_path))


def test_empty_result_is_not_recorded_as_complete(tmp_path):
    """빈 번역문이 올라오면 메인 루프가 완료로 기록하지 않는다.

    아래층 방어가 뚫렸을 때의 두 번째 방어선이다. 완료로 기록되면 이어하기가 그 청크를
    다시 집지 않아 결과물에 구멍이 남는다.
    """
    source = tmp_path / "input.txt"
    source.write_text("This is a test chunk.", encoding="utf-8")
    output = tmp_path / "output.txt"

    async def return_empty(self, chunk_text, *args, **kwargs):
        return ""

    _run_translation(source, output, return_empty)

    metadata = json.loads(get_metadata_file_path(source).read_text(encoding="utf-8"))
    assert metadata["translated_chunks"] == {}
    assert sorted(metadata.get("failed_chunks", {})) == ["0"]
    assert "비어있습니다" in json.dumps(metadata, ensure_ascii=False)


def test_empty_result_keeps_the_source_text(tmp_path):
    """실패한 청크 자리에는 원문이 남아 무엇이 빠졌는지 알 수 있다."""
    source = tmp_path / "input.txt"
    source.write_text("This is a test chunk.", encoding="utf-8")
    output = tmp_path / "output.txt"

    async def return_empty(self, chunk_text, *args, **kwargs):
        return ""

    _run_translation(source, output, return_empty)

    assert "This is a test chunk." in output.read_text(encoding="utf-8")


def test_whitespace_only_chunk_still_succeeds(tmp_path):
    """원문이 공백뿐이면 빈 번역문이 정상이다. 이것까지 실패로 만들면 안 된다."""
    source = tmp_path / "input.txt"
    source.write_text("   \n   ", encoding="utf-8")
    output = tmp_path / "output.txt"

    async def return_empty(self, chunk_text, *args, **kwargs):
        return ""

    _run_translation(source, output, return_empty)

    metadata = json.loads(get_metadata_file_path(source).read_text(encoding="utf-8"))
    assert metadata.get("failed_chunks", {}) == {}
