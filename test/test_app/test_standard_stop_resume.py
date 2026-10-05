"""표준 모드에서 번역이 중간에 멈춘 뒤 결과물의 뒤쪽이 원문과 어긋나지 않는지 검증한다.

제보: "번역이 중단되면 중단된 뒤쪽 원문 배열이 망가진다." 경로가 둘이었다.

1. 키·재시도 소진이 작업을 멈추지 못했다. 청크 하나의 API 실패로 기록돼 남은 청크가 전부
   "실패 문구 + 원문"으로 채워졌고, 그대로 병합돼 완료로 끝났다. 출력 뒤쪽은 청크마다 오류 문구로
   잘린 원문이 되었다.
2. 사용자가 중단해도 청크 Task가 살아남았다. 곧바로 다시 시작하면 취소 신호가 지워져, 세마포어에서
   기다리던 이전 Task가 새 실행과 같은 청크를 겹쳐 번역했다.
"""
import asyncio
import json

import pytest

from app.app_service import AppService
from core.exceptions import BtgApiClientException
from domain.translation_service import TranslationService
from infrastructure.file_handler import get_metadata_file_path
from infrastructure.gemini_client import GeminiAllApiKeysExhaustedException

LINES = [f"line {i:02d} source text." for i in range(40)]


def _service(chunk_size: int, max_workers: int) -> AppService:
    service = AppService()
    service.load_app_config(
        runtime_overrides={
            "chunk_size": chunk_size,
            "api_keys": ["dummy-key-for-init"],
            "max_workers": max_workers,
            "translation_mode": "standard",
            "enable_translation_memory": False,
            "enable_memory_extraction": False,
            "enable_post_processing": False,
        }
    )
    return service


def _exhausted() -> BtgApiClientException:
    return BtgApiClientException(
        "모든 API 키를 사용했으나 요청에 실패했습니다.",
        original_exception=GeminiAllApiKeysExhaustedException("all keys exhausted"),
    )


def _write_source(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return source, tmp_path / "input_translated.txt"


def test_keys_exhausted_stops_the_job_and_resume_finishes_it(tmp_path):
    """키가 모두 소진되면 작업이 멈추고, 남은 청크는 이어하기에서 번역된다."""
    source, output = _write_source(tmp_path)
    calls = {"n": 0}

    async def exhaust_after_two(self, chunk_text, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 2:
            raise _exhausted()
        return chunk_text.replace("source", "TRANSLATED")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", exhaust_after_two)
        with pytest.raises(BtgApiClientException):
            asyncio.run(_service(chunk_size=100, max_workers=1).start_translation_async(source, output))

    # 소진 이후 청크를 하나도 시도하지 않고 멈췄다
    assert calls["n"] == 3
    metadata = json.loads(get_metadata_file_path(source).read_text(encoding="utf-8"))
    assert sorted(metadata["translated_chunks"], key=int) == ["0", "1"]
    # 소진은 청크 실패가 아니다. 실패로 남기면 "실패 + 원문"이 결과물에 병합된다
    assert metadata.get("failed_chunks", {}) == {}
    assert metadata["status"] != "completed"
    assert not output.exists() or "번역 실패" not in output.read_text(encoding="utf-8")

    async def translate_all(self, chunk_text, *args, **kwargs):
        return chunk_text.replace("source", "TRANSLATED")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", translate_all)
        asyncio.run(_service(chunk_size=100, max_workers=1).start_translation_async(source, output))

    result = output.read_text(encoding="utf-8")
    assert "source" not in result and "번역 실패" not in result
    # 줄 순서와 개수가 원문과 같다
    assert [line for line in result.splitlines() if line.strip()] == [
        line.replace("source", "TRANSLATED") for line in LINES
    ]


def test_keys_exhausted_cancels_in_flight_chunks(tmp_path):
    """동시 작업 중 한 청크가 소진을 만나면 나머지 청크 Task도 남지 않고 정리된다."""
    source, output = _write_source(tmp_path)
    calls = {"n": 0}

    async def exhaust_on_third(self, chunk_text, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise _exhausted()
        await asyncio.sleep(0.2)
        return chunk_text

    async def run():
        with pytest.raises(BtgApiClientException):
            await _service(chunk_size=100, max_workers=3).start_translation_async(source, output)
        await asyncio.sleep(0.5)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", exhaust_on_third)
        leftover = asyncio.run(run())

    assert leftover == []
    assert calls["n"] == 3


def test_cancel_then_immediate_restart_does_not_translate_chunks_twice(tmp_path):
    """중단 직후 다시 시작해도 이전 실행의 청크 Task가 같은 청크를 겹쳐 번역하지 않는다."""
    source = tmp_path / "input.txt"
    source.write_text("".join(f"line {i:02d} source text.\n" for i in range(40)), encoding="utf-8")
    output = tmp_path / "input_translated.txt"
    calls = []

    async def slow_translate(self, chunk_text, *args, **kwargs):
        calls.append(chunk_text)
        await asyncio.sleep(0.3)
        return chunk_text.replace("source", "TRANSLATED")

    async def run():
        service = _service(chunk_size=100, max_workers=3)
        first = asyncio.create_task(service.start_translation_async(source, output))
        await asyncio.sleep(0.45)  # 첫 3개가 끝나고 다음 3개가 진행 중일 때 중단
        await service.cancel_translation_async()
        with pytest.raises(asyncio.CancelledError):
            await first
        alive_after_cancel = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        await service.start_translation_async(source, output)
        await asyncio.sleep(1.0)  # 살아남은 Task가 있다면 이 사이에 번역을 더 보낸다
        return alive_after_cancel

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TranslationService, "translate_chunk_async", slow_translate)
        alive_after_cancel = asyncio.run(run())

    assert alive_after_cancel == []
    total_chunks = 10
    # 중단 때 진행 중이던 청크(최대 3개)만 다시 번역된다. 예전에는 기다리던 청크까지 두 번 번역했다.
    assert len(calls) <= total_chunks + 3
    result = output.read_text(encoding="utf-8")
    assert "source" not in result
    assert [line for line in result.splitlines() if line.strip()] == [
        f"line {i:02d} TRANSLATED text." for i in range(40)
    ]
