"""이어하기 검증: 실패한 청크는 완료로 기록되지 않고, 재실행 때 그 청크만 다시 처리된다.

이 파일은 원래 동기 API를 쓰는 수동 실행 스크립트였다. 비동기 마이그레이션 이후 유효하지
않다는 주석과 함께 비활성화를 시도했으나 함수 정의 줄만 주석 처리되고 본문이 남아
`IndentationError`로 수집조차 되지 않았다. 검증하려던 동작 자체는 여전히 중요하다.
과부하(503)로 청크가 실패했을 때 이어하기가 그 청크만 다시 집어 오는지가 이 프로젝트의
실사용 경로이기 때문이다.

`translate_chunk_async`를 가로채 API를 부르지 않으며, 파일은 전부 tmp_path 안에서 다룬다.
"""
import asyncio
import json

import pytest

from app.app_service import AppService
from core.exceptions import BtgTranslationException
from domain.translation_service import TranslationService
from infrastructure.file_handler import get_metadata_file_path

# 각 줄이 별도 청크가 되는 크기. 가장 긴 줄이 29자다.
CHUNK_SIZE = 30
FAILING_MARKER = "will fail"

SOURCE_TEXT = "first chunk.\nsecond chunk which will fail.\nthird chunk."


@pytest.fixture
def source_file(tmp_path):
    path = tmp_path / "resume_input.txt"
    path.write_text(SOURCE_TEXT, encoding="utf-8")
    return path


def _app_service():
    service = AppService()
    service.load_app_config(
        runtime_overrides={
            "chunk_size": CHUNK_SIZE,
            "api_keys": ["dummy-key-for-init"],
            "max_workers": 1,
            "translation_mode": "standard",
        }
    )
    return service


def _read_metadata(source_path):
    return json.loads(get_metadata_file_path(source_path).read_text(encoding="utf-8"))


def _run_translation(source_path, output_path, fake_translate):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", fake_translate)
        asyncio.run(_app_service().start_translation_async(source_path, output_path))


def test_failed_chunk_is_not_marked_complete(source_file, tmp_path):
    output_path = tmp_path / "resume_output.txt"
    seen = []

    async def fail_the_second(self, chunk_text, *args, **kwargs):
        seen.append(chunk_text)
        if FAILING_MARKER in chunk_text:
            raise BtgTranslationException("테스트를 위한 의도된 실패")
        return f"[T] {chunk_text}"

    _run_translation(source_file, output_path, fail_the_second)

    assert len(seen) == 3
    metadata = _read_metadata(source_file)
    assert sorted(metadata["translated_chunks"]) == ["0", "2"]
    assert sorted(metadata.get("failed_chunks", {})) == ["1"]


def test_resume_retries_only_the_failed_chunk(source_file, tmp_path):
    output_path = tmp_path / "resume_output.txt"

    async def fail_the_second(self, chunk_text, *args, **kwargs):
        if FAILING_MARKER in chunk_text:
            raise BtgTranslationException("테스트를 위한 의도된 실패")
        return f"[T] {chunk_text}"

    _run_translation(source_file, output_path, fail_the_second)

    retried = []

    async def succeed_everywhere(self, chunk_text, *args, **kwargs):
        retried.append(chunk_text)
        return f"[R] {chunk_text}"

    _run_translation(source_file, output_path, succeed_everywhere)

    # 두 번째 실행은 실패했던 청크 하나만 집어야 한다.
    assert len(retried) == 1
    assert FAILING_MARKER in retried[0]

    metadata = _read_metadata(source_file)
    assert sorted(metadata["translated_chunks"]) == ["0", "1", "2"]

    # 1차에서 성공한 청크는 그대로 남고, 실패했던 자리만 재번역 결과로 채워진다.
    output = output_path.read_text(encoding="utf-8")
    assert "[T] first chunk." in output
    assert "[R] second chunk which will fail." in output
    assert "[T] third chunk." in output
