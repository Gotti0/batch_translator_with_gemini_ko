"""서브 청크 분할 재번역이 순서대로(동시 1개) 처리되는지 확인한다 (RPM 제어 리팩터 T7).

동시 작업 수는 가장 바깥 진입점(앱 번역 루프, 검수 탭)만 제한하고, 안쪽 분할 경로는 세마포어를 새로 열지 않는다.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.exceptions import BtgTranslationException
from domain.review_providers.epub_provider import EpubReviewProvider
from domain.translation_service import TranslationService


class ConcurrencyProbe:
    """호출 중인 수를 세어 최대 동시 진행 수와 호출 순서를 기록한다."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = []

    async def enter(self, item):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(item)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1


def make_service(probe, fail_first_for=None):
    service = TranslationService(MagicMock(), {"model_name": "m", "max_workers": 4})
    failed = []

    async def fake_translate(text, stream=False):
        await probe.enter(text)
        if fail_first_for and fail_first_for in text and not failed:  # 전체에서 한 번만 실패
            failed.append(text)
            raise BtgTranslationException("콘텐츠 안전 문제로 번역할 수 없습니다. (테스트)")
        return f"T({text.strip()[:12]})"

    service.translate_text_async = fake_translate
    return service


TEXT = "\n".join(f"문장{i:02d} 입니다. 내용을 충분히 길게 만들기 위한 문장입니다." for i in range(40))


def test_subchunks_are_translated_one_at_a_time_in_order():
    probe = ConcurrencyProbe()
    service = make_service(probe)

    result = asyncio.run(service.translate_text_force_split_async(TEXT, split_level=2, min_chunk_size=20))

    assert probe.max_active == 1
    assert len(probe.calls) >= 4
    # 결과는 원래 순서대로 이어 붙는다
    starts = [result.index(f"T({call.strip()[:12]})") for call in probe.calls]
    assert starts == sorted(starts)


def test_recursive_split_after_safety_error_stays_sequential():
    probe = ConcurrencyProbe()
    service = make_service(probe, fail_first_for="문장05")

    result = asyncio.run(service.translate_text_force_split_async(TEXT, split_level=1, min_chunk_size=20))

    assert probe.max_active == 1
    assert "실패" not in result and "오류" not in result
    assert any("문장05" in c for c in probe.calls[1:])  # 실패한 서브 청크가 더 잘게 나뉘어 다시 번역됨


def test_stop_request_between_subchunks_cancels_the_chunk():
    probe = ConcurrencyProbe()
    service = make_service(probe)
    service.stop_check_callback = lambda: len(probe.calls) >= 1

    with pytest.raises(BtgTranslationException, match="취소"):
        asyncio.run(service.translate_text_force_split_async(TEXT, split_level=2, min_chunk_size=20))

    assert len(probe.calls) == 1


def test_task_cancellation_propagates():
    probe = ConcurrencyProbe()
    service = make_service(probe)

    async def scenario():
        task = asyncio.ensure_future(service.translate_text_force_split_async(TEXT, split_level=2, min_chunk_size=20))
        await asyncio.sleep(0.005)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())


def make_epub_provider(probe, fail_units=()):
    provider = EpubReviewProvider(MagicMock())

    async def fake_integrity(units):
        await probe.enter([u.id for u in units])
        if any(u.id in fail_units for u in units):
            raise RuntimeError("boom")
        return {u.id: f"번역:{u.text}" for u in units}

    provider.translation_service = MagicMock()
    provider.translation_service._translate_integrity_chunk_with_retry = fake_integrity
    return provider


def test_epub_subchunks_run_sequentially_and_keep_source_on_failure():
    probe = ConcurrencyProbe()
    provider = make_epub_provider(probe, fail_units={"5"})
    source = "\n".join(f"줄{i}" for i in range(8))

    result = asyncio.run(provider.retranslate_chunk("0", source, split_level=2)).splitlines()

    assert probe.max_active == 1
    assert len(probe.calls) == 4
    assert result[5] == "줄5" and result[4] == "줄4"  # 실패한 서브 청크(4~5번 줄)는 원문 유지
    assert result[0] == "번역:줄0" and result[7] == "번역:줄7"
