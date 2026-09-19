"""용어집 추출에서 과부하(503)로 실패한 세그먼트를 끝에 한 번 더 시도하고, 끝내 빠진 세그먼트를 알리는지 확인한다.

503 재시도가 요청당 1회로 줄면서, 과부하 순간에 걸린 세그먼트가 결과에서 조용히 빠지던 결함(T8 실측)을 고정한다.
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


def test_overloaded_segment_is_retried_at_the_end(service, caplog):
    fake = FakeGlossaryService({"seg-1": [overloaded()]})

    with caplog.at_level(logging.WARNING):
        run(service, fake)

    assert sorted(fake.calls[:4]) == SEGMENTS
    assert fake.calls[4:] == ["seg-1"]  # 첫 순회가 끝난 뒤 다시 시도
    assert sorted(fake.finalized) == sorted(f"term:{s}" for s in SEGMENTS)
    assert not any("결과에서 빠졌습니다" in r.getMessage() for r in caplog.records)


def test_segment_still_failing_after_retry_is_reported(service, caplog):
    fake = FakeGlossaryService({"seg-1": [overloaded(), overloaded()]})
    progress = []

    with caplog.at_level(logging.WARNING):
        run(service, fake, progress.append)

    assert fake.calls.count("seg-1") == 2
    assert "term:seg-1" not in fake.finalized
    warnings = [r.getMessage() for r in caplog.records if "결과에서 빠졌습니다" in r.getMessage()]
    assert len(warnings) == 1 and "1/4" in warnings[0] and "[1]" in warnings[0]
    assert progress[-1].current_status_message == "완료 (세그먼트 1개 누락)"


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
