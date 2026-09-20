"""용어집 추출에서 과부하(503)가 난 세그먼트를 성공할 때까지 다시 시도하는지 확인한다.

예전에는 세그먼트를 건너뛴 뒤 끝에 한 번만 더 시도했고, 그래도 실패하면 그 부분의 용어가
최종 용어집에서 빠졌다. 지금은 무결성 번역 청크와 같은 방침으로 상한 없이 다시 시도하며,
멈추는 판단은 취소 요청에 맡긴다. 검열 같은 다른 오류는 여전히 재시도하지 않고 누락을 알린다.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.app_service import AppService
from core.exceptions import BtgApiClientException
from infrastructure.gemini_client import GeminiContentSafetyException, GeminiServiceUnavailableException

SEGMENTS = ["seg-1", "seg-2", "seg-3", "seg-4"]


def overloaded():
    inner = GeminiServiceUnavailableException("모델 과부하(503)로 키#1에서 2회 시도했으나 실패")
    return BtgApiClientException(f"용어집 추출 API 호출 최종 실패: {inner}", original_exception=inner)


def censored():
    inner = GeminiContentSafetyException("콘텐츠 안전 문제로 응답 차단")
    return BtgApiClientException(f"용어집 추출 API 호출 최종 실패: {inner}", original_exception=inner)


class FakeGlossaryService:
    """script[segment]는 호출마다 꺼낼 결과 목록(예외 또는 항목 리스트)."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.finalized = None

    def load_seed_glossary(self, path):
        return []

    def prepare_segments(self, text):
        return list(SEGMENTS)

    async def _extract_glossary_entries_from_segment_via_api_async(self, segment, prompt=None, stop_check=None):
        self.calls.append(segment)
        await asyncio.sleep(0)
        outcome = self.script[segment].pop(0) if self.script.get(segment) else [f"term:{segment}"]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def finalize_glossary(self, entries, seeds):
        self.finalized = list(entries)
        return self.finalized

    def get_glossary_output_path(self, input_path):
        return Path(input_path).with_suffix(".glossary.json")

    def save_glossary_to_json(self, entries, path):
        pass


@pytest.fixture
def service(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"api_keys": ["fake-key"], "model_name": "gemini-test", "max_workers": 2}))
    svc = AppService(config_file_path=str(config_path))
    svc.extract_text_from_file = lambda path: "본문"
    return svc


def run(svc, fake, progress=None):
    svc.glossary_service = fake
    return asyncio.run(svc._do_glossary_extraction_async("input.txt", progress_callback=progress))


def test_overloaded_segment_is_retried_until_it_succeeds(service, caplog):
    """과부하가 풀릴 때까지 같은 세그먼트를 다시 부르고, 결과에서 빠지지 않는다."""
    fake = FakeGlossaryService({"seg-1": [overloaded(), overloaded()]})

    with caplog.at_level(logging.WARNING):
        run(service, fake)

    # 실패 2회 + 성공 1회.
    assert fake.calls.count("seg-1") == 3
    assert sorted(fake.finalized) == sorted(f"term:{s}" for s in SEGMENTS)
    assert not any("결과에서 빠졌습니다" in r.getMessage() for r in caplog.records)


def test_overloaded_segment_is_not_dropped_after_one_extra_try(service, caplog):
    """한 번 더 시도하고 마는 것이 아니라, 풀릴 때까지 계속 시도한다."""
    fake = FakeGlossaryService({"seg-1": [overloaded()] * 5})
    progress = []

    with caplog.at_level(logging.WARNING):
        run(service, fake, progress.append)

    assert fake.calls.count("seg-1") == 6
    assert "term:seg-1" in fake.finalized
    assert not any("결과에서 빠졌습니다" in r.getMessage() for r in caplog.records)
    assert progress[-1].current_status_message == "완료"


def test_cancellation_breaks_out_of_the_retry_loop(service):
    """상한이 없으므로 취소 요청이 유일한 탈출구다. 재시도 중에도 먹혀야 한다."""
    fake = FakeGlossaryService({"seg-1": [overloaded()] * 50})
    original = fake._extract_glossary_entries_from_segment_via_api_async
    attempts = {"n": 0}

    async def cancel_during_retry(segment, prompt=None, stop_check=None):
        if segment == "seg-1":
            attempts["n"] += 1
            if attempts["n"] >= 3:
                service.cancel_glossary_event.set()
        return await original(segment, prompt, stop_check)

    fake._extract_glossary_entries_from_segment_via_api_async = cancel_during_retry

    with pytest.raises(asyncio.CancelledError):
        run(service, fake)

    # 무한히 돌지 않고 취소 시점에서 멈췄다.
    assert attempts["n"] <= 4


def test_non_overload_failure_is_not_retried_but_reported(service, caplog):
    """검열 같은 오류는 다시 보내도 결과가 같을 가능성이 높아 재시도하지 않되, 누락은 알린다."""
    fake = FakeGlossaryService({"seg-2": [censored()]})

    with caplog.at_level(logging.WARNING):
        run(service, fake)

    assert fake.calls.count("seg-2") == 1
    assert len(fake.calls) == 4
    assert any("세그먼트 1/4개가 실패" in r.getMessage() and "[2]" in r.getMessage() for r in caplog.records)


def test_all_segments_succeed_without_retry(service):
    fake = FakeGlossaryService({})
    progress = []

    run(service, fake, progress.append)

    assert sorted(fake.calls) == SEGMENTS
    assert progress[-1].current_status_message == "완료"


def test_overload_detection_walks_the_exception_chain():
    assert AppService._is_overload_failure(overloaded())
    assert not AppService._is_overload_failure(censored())
    try:
        try:
            raise GeminiServiceUnavailableException("503")
        except GeminiServiceUnavailableException as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert AppService._is_overload_failure(outer)


def test_cancellation_still_saves_partial_results(service):
    """취소되면 재시도 없이 멈추고, 그때까지 추출한 항목은 저장한다(기존 동작 유지)."""
    fake = FakeGlossaryService({})
    original = fake._extract_glossary_entries_from_segment_via_api_async

    async def cancel_after_first(segment, prompt=None, stop_check=None):
        result = await original(segment, prompt, stop_check)
        service.cancel_glossary_event.set()
        return result

    fake._extract_glossary_entries_from_segment_via_api_async = cancel_after_first

    with pytest.raises(asyncio.CancelledError):
        run(service, fake)

    assert fake.finalized and len(fake.finalized) < len(SEGMENTS)
