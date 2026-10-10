"""
배치 번역 모드 통합 테스트.

GeminiBatchClient를 메모리 구현(FakeBatchServer)으로 바꿔 끼워, 제출 → 앱 재시작 → 상태 조회 →
수거 → 미완료 청크 처리(실시간 마무리 / 실패 표시 저장 / 재제출)까지 AppService를 통해 검증한다.
"""

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from google.genai import types as t

from app.app_service import AppService
from app.batch_translation_service import (
    BLOCKED_PREFIX,
    BatchTranslationService,
    chunk_key,
    group_requests_by_size,
    parse_chunk_key,
)
from core.exceptions import BtgServiceException
from infrastructure.file_handler import load_metadata, save_metadata
from infrastructure.gemini_batch_client import BatchItemResult, BatchJobInfo, BatchJobState, key_fingerprint

PARAGRAPHS = [f"第{i}段落。勇者は剣を抜いた。" * 3 for i in range(6)]


class FakeBatchServer:
    """배치 작업·업로드를 메모리에 두는 가짜 서버. 테스트가 상태 전이를 직접 조종한다."""

    def __init__(self):
        self.jobs: Dict[str, dict] = {}
        self.uploads: List[dict] = []
        self.cancelled: List[str] = []
        self.clients_by_fingerprint: Dict[str, int] = {}
        self.fail_submit = False

    def client(self, api_key: str) -> "FakeBatchClient":
        fp = key_fingerprint(api_key)
        self.clients_by_fingerprint[fp] = self.clients_by_fingerprint.get(fp, 0) + 1
        return FakeBatchClient(self, fp)

    def complete(self, name: str, responder: Callable[[int, t.InlinedRequest], BatchItemResult],
                 state: BatchJobState = BatchJobState.SUCCEEDED) -> None:
        job = self.jobs[name]
        job["state"] = state
        job["results"] = [responder(parse_chunk_key(r.metadata["key"]), r) for r in job["requests"]]

    def only_job(self) -> str:
        assert len(self.jobs) == 1
        return next(iter(self.jobs))


class FakeBatchClient:
    def __init__(self, server: FakeBatchServer, fingerprint: str):
        self.server = server
        self.fingerprint = fingerprint

    def _info(self, name: str) -> BatchJobInfo:
        job = self.server.jobs[name]
        return BatchJobInfo(
            name=name, display_name=job["display_name"], state=job["state"], raw_state=job["state"].name,
            results=job.get("results") if job["state"].is_terminal else None,
        )

    async def submit(self, model, requests, display_name):
        if self.server.fail_submit:
            raise RuntimeError("network down")
        name = f"batches/{len(self.server.jobs) + 1}"
        self.server.jobs[name] = {
            "display_name": display_name, "requests": list(requests), "state": BatchJobState.PENDING,
            "model": model, "fingerprint": self.fingerprint,
        }
        return self._info(name)

    async def get(self, name):
        return self._info(name)

    async def cancel(self, name):
        self.server.cancelled.append(name)
        self.server.jobs[name]["state"] = BatchJobState.CANCELLED
        self.server.jobs[name].setdefault("results", [])

    async def find_by_display_name(self, display_name):
        for name, job in self.server.jobs.items():
            if job["display_name"] == display_name:
                return self._info(name)
        return None

    async def upload_file(self, data, mime_type, display_name):
        n = len(self.server.uploads) + 1
        self.server.uploads.append({"data": data, "mime_type": mime_type})
        return t.File(name=f"files/f{n}", uri=f"https://example.invalid/files/f{n}", mime_type=mime_type)


def _translate_all(idx, req):
    return BatchItemResult(key=chunk_key(idx), text=f"번역{idx}")


@pytest.fixture
def workspace(tmp_path):
    input_path = tmp_path / "novel.txt"
    input_path.write_text("\n\n".join(PARAGRAPHS), encoding="utf-8")
    config = {
        "llm_provider": "gemini",
        "api_keys": ["key-one", "key-two"],
        "use_vertex_ai": False,
        "model_name": "gemini-3.8-flash",
        "translation_mode": "batch",
        "chunk_size": 60,
        "prompts": "Translate: {{slot}}",
        "enable_pagefold": False,
        "enable_prefill_translation": False,
        "enable_dynamic_glossary_injection": False,
        "enable_post_processing": False,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return {"input": input_path, "output": tmp_path / "novel_ko.txt", "config": config_path, "dir": tmp_path}


def _make_app(workspace, server) -> AppService:
    app = AppService(workspace["config"])
    app.batch_client_factory = server.client
    return app


@pytest.mark.asyncio
async def test_submit_restart_collect_and_finish_realtime(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    total = len(app.chunk_service.create_chunks_from_file_content(workspace["input"].read_text(encoding="utf-8"), 60))
    assert total >= 3

    # 1. 시작 버튼 → 제출 (결과 없음)
    await app.start_translation_async(workspace["input"], workspace["output"])
    name = server.only_job()
    job = server.jobs[name]
    assert len(job["requests"]) == total
    assert job["model"] == "gemini-3.8-flash"
    assert job["fingerprint"] == key_fingerprint("key-one")  # 첫 키로 제출

    req0 = job["requests"][0]
    assert req0.metadata == {"key": "chunk-00000"}
    assert req0.contents[0].parts[0].text.startswith("Translate: 第0段落")
    assert req0.config.http_options is None and req0.config.automatic_function_calling is None
    assert len(req0.config.safety_settings) == 5

    batch = load_metadata(workspace["input"])["batch"]
    assert batch["jobs"][0]["name"] == name and batch["jobs"][0]["collected"] is False
    assert "key-one" not in json.dumps(batch)  # 키 자체는 저장하지 않는다

    # 2. 앱 재시작 후 다시 시작 버튼 → 재제출하지 않고 조회만 한다
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    assert len(server.jobs) == 1
    summary = app.get_batch_summary(workspace["input"])
    assert summary.active and summary.translated == 0

    # 3. 작업 완료: 청크 1은 검열로 빈 응답
    def responder(idx, req):
        if idx == 1:
            return BatchItemResult(key=chunk_key(idx), blocked=True, finish_reason="PROHIBITED_CONTENT", error="응답 차단")
        return _translate_all(idx, req)

    server.complete(name, responder)
    summary = await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert not summary.active
    assert summary.remaining == [1]
    assert summary.blocked == 1
    assert summary.translated == total - 1
    assert not workspace["output"].exists() or workspace["output"].read_text(encoding="utf-8") == ""
    failed = load_metadata(workspace["input"])["failed_chunks"]
    assert failed["1"]["error"].startswith(BLOCKED_PREFIX)

    # 수거한 작업은 다시 조회해도 두 번 쓰지 않는다
    summary = await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert summary.translated == total - 1

    # 4. 실시간으로 마무리: 표준 모드 이어하기가 미완료 청크 1개만 번역한다
    app.gemini_client.generate_text_async = AsyncMock(return_value="실시간 번역1")
    await app.start_translation_async(workspace["input"], workspace["output"], translation_mode_override="standard")
    assert app.gemini_client.generate_text_async.await_count == 1

    result = workspace["output"].read_text(encoding="utf-8")
    expected = [f"번역{i}" if i != 1 else "실시간 번역1" for i in range(total)]
    positions = [result.index(e) for e in expected]
    assert positions == sorted(positions)
    assert load_metadata(workspace["input"])["status"] == "completed"


@pytest.mark.asyncio
async def test_all_success_writes_final_file_on_refresh(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    server.complete(server.only_job(), _translate_all)

    summary = await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert summary.complete
    text = workspace["output"].read_text(encoding="utf-8")
    assert "번역0" in text and "##CHUNK_INDEX" not in text
    assert load_metadata(workspace["input"])["status"] == "completed"


@pytest.mark.asyncio
async def test_save_with_failures_and_resubmit(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    name = server.only_job()
    # 작업 만료: 결과 없음 → 모든 청크 미완료
    server.complete(name, _translate_all, state=BatchJobState.EXPIRED)
    server.jobs[name]["results"] = []
    summary = await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert summary.errored == summary.total_chunks and summary.remaining

    # 시작 버튼은 이미 시도한 청크를 자동으로 다시 제출하지 않는다
    await app.start_translation_async(workspace["input"], workspace["output"])
    assert len(server.jobs) == 1

    # 다시 배치 제출: 2라운드, 미완료 청크만
    summary = await app.resubmit_batch_async(workspace["input"])
    assert summary.round == 2 and summary.active and len(server.jobs) == 2
    second = [n for n in server.jobs if n != name][0]
    assert server.jobs[second]["display_name"].endswith("-r2-1")

    # 두 번째 라운드 중 하나만 성공한 뒤 "그대로 저장"
    server.complete(second, lambda idx, req: _translate_all(idx, req) if idx == 0 else BatchItemResult(key=chunk_key(idx), error="500: internal"))
    await app.refresh_batch_async(workspace["input"], workspace["output"])
    filled = await app.save_batch_with_failures_async(workspace["input"], workspace["output"])
    assert filled == summary.total_chunks - 1
    text = workspace["output"].read_text(encoding="utf-8")
    assert "번역0" in text
    assert "[번역 실패:" in text and "第1段落" in text


@pytest.mark.asyncio
async def test_cancel(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    summary = await app.cancel_batch_async(workspace["input"])
    assert server.cancelled == [server.only_job()]
    assert not summary.active and len(summary.remaining) == summary.total_chunks


@pytest.mark.asyncio
async def test_recovers_job_submitted_before_crash(workspace):
    """제출 응답을 받기 전에 앱이 꺼진 경우 display_name으로 작업을 찾아 이어간다"""
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    md = load_metadata(workspace["input"])
    md["batch"]["jobs"][0]["name"] = None
    save_metadata(workspace["input"], md)

    server.complete(server.only_job(), _translate_all)
    summary = await _make_app(workspace, server).refresh_batch_async(workspace["input"], workspace["output"])
    assert summary.complete
    assert load_metadata(workspace["input"])["batch"]["jobs"][0]["name"] == server.only_job()


@pytest.mark.asyncio
async def test_submit_failure_is_recorded_and_retryable(workspace):
    server = FakeBatchServer()
    server.fail_submit = True
    app = _make_app(workspace, server)
    with pytest.raises(RuntimeError):
        await app.start_translation_async(workspace["input"], workspace["output"])
    job = load_metadata(workspace["input"])["batch"]["jobs"][0]
    assert job["state"] == "SUBMIT_FAILED" and job["collected"] is True

    server.fail_submit = False
    await app.start_translation_async(workspace["input"], workspace["output"])
    assert len(server.jobs) == 1


@pytest.mark.asyncio
async def test_missing_submit_key_is_reported(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])

    app.config["api_keys"] = ["another-key"]
    with pytest.raises(BtgServiceException) as ctx:
        await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert "제출한 API 키" in str(ctx.value)


@pytest.mark.asyncio
async def test_jobs_split_by_size_and_oversized_chunk_left_for_realtime(workspace):
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    service = app._get_batch_service()
    one = service._build_inlined_request(0, PARAGRAPHS[0], {})
    from app.batch_translation_service import estimate_request_bytes
    size = estimate_request_bytes(one)

    app.config["batch_max_request_bytes"] = int(size * 2.5)
    await app.start_translation_async(workspace["input"], workspace["output"])
    sizes = [len(j["requests"]) for j in server.jobs.values()]
    total = len(load_metadata(workspace["input"])["batch"]["jobs"][0]["chunks"]) + sum(sizes[1:])
    assert len(server.jobs) >= 2 and max(sizes) <= 2
    assert sum(sizes) == total
    # 작업별 청크 목록이 서로 겹치지 않고 순서대로 이어진다
    chunks = [c for j in load_metadata(workspace["input"])["batch"]["jobs"] for c in j["chunks"]]
    assert chunks == sorted(chunks) and len(chunks) == len(set(chunks))

    # 청크 하나보다 작은 한도면 청크가 제출되지 않고 실패로 남아 실시간 마무리 대상이 된다
    await app.cancel_batch_async(workspace["input"])
    server2 = FakeBatchServer()
    app.batch_client_factory = server2.client
    app.config["batch_max_request_bytes"] = 10
    summary = await app.resubmit_batch_async(workspace["input"])
    assert not server2.jobs and summary.errored == summary.total_chunks


def test_group_requests_by_size():
    groups, oversized = group_requests_by_size([(0, "a", 5), (1, "b", 5), (2, "c", 20), (3, "d", 4), (4, "e", 6)], 10)
    assert [[i for i, _ in g] for g in groups] == [[0, 1], [3, 4]]
    assert oversized == [2]


@pytest.mark.asyncio
async def test_pagefold_glossary_uploaded_once(workspace):
    glossary = workspace["dir"] / "novel_simple_glossary.json"
    glossary.write_text(json.dumps([
        {"keyword": "勇者", "translated_keyword": "용사", "target_language": "ko", "occurrence_count": 5}
    ], ensure_ascii=False), encoding="utf-8")
    cfg = json.loads(workspace["config"].read_text(encoding="utf-8"))
    cfg.update({"enable_pagefold": True, "pagefold_mode": "reference", "target_translation_language": "ko"})
    workspace["config"].write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    server = FakeBatchServer()
    app = _make_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])

    assert len(server.uploads) == 1 and server.uploads[0]["data"].startswith(b"%PDF")
    requests = server.jobs[server.only_job()]["requests"]
    uris = {r.contents[0].parts[0].file_data.file_uri for r in requests}
    assert uris == {"https://example.invalid/files/f1"}
    assert all(r.config.media_resolution == t.MediaResolution.MEDIA_RESOLUTION_LOW for r in requests)
    assert load_metadata(workspace["input"])["batch"]["uploaded_files"] == ["files/f1"]


def test_unavailable_reasons(workspace):
    app = _make_app(workspace, FakeBatchServer())
    assert app.batch_unavailable_reason() is None

    app.config["use_vertex_ai"] = True
    assert "Vertex" in app.batch_unavailable_reason()
    app.config["use_vertex_ai"] = False
    app.config["api_keys"] = []
    app.config["api_key"] = ""
    assert "API 키" in app.batch_unavailable_reason()


def test_cli_batch_actions(workspace, capsys):
    """main_cli의 --batch-submit / --batch-status / --batch-finish keep 흐름"""
    import argparse
    from main_cli import run_batch_cli_action

    server = FakeBatchServer()
    app = _make_app(workspace, server)

    def ns(**kw):
        base = {"batch_submit": False, "batch_status": False, "batch_finish": None}
        base.update(kw)
        return argparse.Namespace(**base)

    run_batch_cli_action(app, ns(batch_submit=True), workspace["input"], workspace["output"])
    assert len(server.jobs) == 1

    run_batch_cli_action(app, ns(batch_status=True), workspace["input"], workspace["output"])
    assert app.get_batch_summary(workspace["input"]).active

    server.complete(server.only_job(), lambda idx, req: _translate_all(idx, req) if idx else BatchItemResult(key=chunk_key(idx), error="x"))
    run_batch_cli_action(app, ns(batch_status=True), workspace["input"], workspace["output"])
    assert "--batch-finish" in capsys.readouterr().out

    run_batch_cli_action(app, ns(batch_finish="keep"), workspace["input"], workspace["output"])
    text = workspace["output"].read_text(encoding="utf-8")
    assert "번역1" in text and "[번역 실패:" in text


@pytest.mark.asyncio
async def test_batch_api_key_is_used_for_submit_and_lookup(workspace):
    """무료 티어 키(로테이션용)와 별도로 지정한 유료 배치 키로 제출·조회한다"""
    server = FakeBatchServer()
    app = _make_app(workspace, server)
    app.config["batch_api_key"] = "paid-key"
    await app.start_translation_async(workspace["input"], workspace["output"])
    assert server.jobs[server.only_job()]["fingerprint"] == key_fingerprint("paid-key")
    assert load_metadata(workspace["input"])["batch"]["key_fingerprint"] == key_fingerprint("paid-key")

    # 로테이션 키 목록이 비어 있어도 배치 키만으로 조회된다
    app.config["api_keys"] = []
    server.complete(server.only_job(), _translate_all)
    summary = await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert summary.complete
# ---------------------------------------------------------------------------
# 무결성 방식 배치 (batch_pipeline = "integrity")
# ---------------------------------------------------------------------------

from app.batch_translation_service import PARTIAL_PREFIX  # noqa: E402

LINES = ["一行目", "二行目", "", "四行目", "五行目", "六行目", "七行目"]


@pytest.fixture
def integrity_workspace(workspace):
    workspace["input"].write_text("\n".join(LINES), encoding="utf-8")
    cfg = json.loads(workspace["config"].read_text(encoding="utf-8"))
    cfg.update({"batch_pipeline": "integrity", "chunk_size": 6000, "integrity_max_items": 3})
    workspace["config"].write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return workspace


def _units(app, workspace):
    return app.translation_service.split_integrity_chunks(workspace["input"].read_text(encoding="utf-8"))


def _json_reply(idx, units, skip=(), prefix="T"):
    """무결성 청크 응답 텍스트. skip에 든 줄 ID는 빼서 누락을 흉내 낸다."""
    items = [{"id": u.id, "translated_text": f"{prefix}{u.id}"} for u in units[idx] if u.text.strip() and u.id not in skip]
    return BatchItemResult(key=chunk_key(idx), text=json.dumps(items, ensure_ascii=False))


def _requested_ids(prompt_contents):
    text = prompt_contents[-1].parts[0].text
    return [str(item["id"]) for item in json.loads(text[text.index("["): text.rindex("]") + 1])]


@pytest.mark.asyncio
async def test_integrity_batch_partial_and_blocked_finish_realtime(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    units = _units(app, ws)
    assert [[u.id for u in c] for c in units] == [["0", "1", "2"], ["3", "4", "5"], ["6"]]

    # 1. 제출: 줄 ID JSON 요청과 응답 스키마가 실린다
    await app.start_translation_async(ws["input"], ws["output"])
    name = server.only_job()
    requests = server.jobs[name]["requests"]
    assert len(requests) == 3
    assert _requested_ids(requests[0].contents) == ["0", "1", "2"]
    assert requests[0].config.response_mime_type == "application/json"
    assert isinstance(requests[0].config.response_schema, t.Schema)
    assert requests[0].config.response_schema.type == t.Type.ARRAY
    md = load_metadata(ws["input"])
    assert md["batch"]["pipeline"] == "integrity" and md["pipeline_type"] == "integrity"
    assert Path(md["batch"]["output_path"]) == ws["output"]

    # 2. 수거: 청크 0 성공, 청크 1은 줄 4 누락, 청크 2는 검열
    def responder(idx, req):
        if idx == 1:
            return _json_reply(idx, units, skip={"4"})
        if idx == 2:
            return BatchItemResult(key=chunk_key(idx), blocked=True, finish_reason="PROHIBITED_CONTENT", error="응답 차단")
        return _json_reply(idx, units)

    server.complete(name, responder)
    summary = await app.refresh_batch_async(ws["input"], ws["output"])
    assert summary.pipeline == "integrity"
    assert summary.remaining == [1, 2] and summary.partial == 1 and summary.blocked == 1
    temp_dir = app.translation_service.integrity_temp_dir_for(ws["output"])
    assert json.loads((temp_dir / "chunk_0.json").read_text(encoding="utf-8")) == {"0": "T0", "1": "T1"}
    assert json.loads((temp_dir / "partial_1.json").read_text(encoding="utf-8")) == {"3": "T3", "5": "T5"}
    assert load_metadata(ws["input"])["failed_chunks"]["1"]["error"].startswith(PARTIAL_PREFIX)
    assert not ws["output"].exists()

    # 3. 실시간으로 마무리: 무결성 모드가 누락 줄 4와 검열 청크 2만 묻는다
    assert app.batch_finish_mode(ws["input"], ws["output"]) == "integrity"
    asked = []

    async def fake_generate(**kwargs):
        ids = _requested_ids(kwargs["prompt"])
        asked.append(ids)
        return [{"id": i, "translated_text": f"R{i}"} for i in ids]

    app.gemini_client.generate_text_async = AsyncMock(side_effect=fake_generate)
    await app.start_translation_async(ws["input"], ws["output"], translation_mode_override="integrity")
    assert asked == [["4"], ["6"]]
    assert ws["output"].read_text(encoding="utf-8").split("\n") == ["T0", "T1", "", "T3", "R4", "T5", "R6"]
    assert not (temp_dir / "partial_1.json").exists()
    assert app.get_batch_summary(ws["input"]).complete


@pytest.mark.asyncio
async def test_integrity_batch_all_success_writes_output_and_uses_integrity_review(integrity_workspace):
    from domain.review_providers.factory import get_review_provider
    from domain.review_providers.integrity_provider import IntegrityReviewProvider

    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    units = _units(app, ws)
    await app.start_translation_async(ws["input"], ws["output"])
    server.complete(server.only_job(), lambda idx, req: _json_reply(idx, units))

    summary = await app.refresh_batch_async(ws["input"], ws["output"])
    assert summary.complete
    assert ws["output"].read_text(encoding="utf-8").split("\n") == ["T0", "T1", "", "T3", "T4", "T5", "T6"]
    assert load_metadata(ws["input"])["status"] == "completed"
    assert isinstance(get_review_provider(str(ws["input"]), app), IntegrityReviewProvider)


@pytest.mark.asyncio
async def test_integrity_batch_keep_keeps_line_positions(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    units = _units(app, ws)
    await app.start_translation_async(ws["input"], ws["output"])
    server.complete(server.only_job(), lambda idx, req: (
        _json_reply(idx, units, skip={"4"}) if idx == 1
        else BatchItemResult(key=chunk_key(idx), text="not json at all") if idx == 2
        else _json_reply(idx, units)
    ))
    summary = await app.refresh_batch_async(ws["input"], ws["output"])
    assert summary.remaining == [1, 2] and summary.errored == 1

    filled = await app.save_batch_with_failures_async(ws["input"], ws["output"])
    assert filled == 2
    # 받은 줄은 쓰고, 빠진 줄과 실패 청크는 원문 줄로 둔다. 실패 표시를 끼워 줄 위치를 바꾸지 않는다.
    assert ws["output"].read_text(encoding="utf-8").split("\n") == ["T0", "T1", "", "T3", "五行目", "T5", "七行目"]


@pytest.mark.asyncio
async def test_integrity_missing_lines_complete_with_source_when_targeted_retry_off(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    app.config["max_integrity_targeted_retry_depth"] = 0
    units = _units(app, ws)
    await app.start_translation_async(ws["input"], ws["output"])
    server.complete(server.only_job(), lambda idx, req: _json_reply(idx, units, skip={"4"}))

    summary = await app.refresh_batch_async(ws["input"], ws["output"])
    assert summary.complete
    assert ws["output"].read_text(encoding="utf-8").split("\n") == ["T0", "T1", "", "T3", "五行目", "T5", "T6"]


@pytest.mark.asyncio
async def test_switching_pipeline_starts_new_batch_and_resubmit_keeps_session_pipeline(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    units = _units(app, ws)

    # 일반 방식으로 제출·수거 (청크 하나 실패)
    app.config["batch_pipeline"] = "standard"
    await app.start_translation_async(ws["input"], ws["output"])
    first = server.only_job()
    server.complete(first, lambda idx, req: _translate_all(idx, req) if idx else BatchItemResult(key=chunk_key(idx), error="x"))
    summary = await app.refresh_batch_async(ws["input"], ws["output"])
    assert summary.pipeline == "standard" and summary.remaining

    # 방식을 무결성으로 바꿔 시작 → 이전 작업 목록을 버리고 새 배치(1라운드)
    app.config["batch_pipeline"] = "integrity"
    await app.start_translation_async(ws["input"], ws["output"])
    second = [n for n in server.jobs if n != first][0]
    batch = load_metadata(ws["input"])["batch"]
    assert batch["pipeline"] == "integrity" and batch["round"] == 1
    assert [j["name"] for j in batch["jobs"]] == [second]
    assert _requested_ids(server.jobs[second]["requests"][0].contents) == ["0", "1", "2"]

    # 재제출은 설정이 아니라 지난 배치의 방식을 잇는다
    server.complete(second, lambda idx, req: _json_reply(idx, units) if idx else BatchItemResult(key=chunk_key(idx), error="x"))
    await app.refresh_batch_async(ws["input"], ws["output"])
    app.config["batch_pipeline"] = "standard"
    summary = await app.resubmit_batch_async(ws["input"])
    assert summary.pipeline == "integrity" and summary.round == 2
    third = [n for n in server.jobs if n not in (first, second)][0]
    assert _requested_ids(server.jobs[third]["requests"][0].contents) == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_integrity_batch_requires_submitted_output_path(integrity_workspace, tmp_path):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    await app.start_translation_async(ws["input"], ws["output"])
    other = tmp_path / "elsewhere.txt"

    with pytest.raises(BtgServiceException) as ctx:
        await app.refresh_batch_async(ws["input"], other)
    assert "출력 경로" in str(ctx.value)

    # 진행 중에는 실시간 마무리도 막는다 (같은 청크를 두 번 과금)
    with pytest.raises(BtgServiceException) as ctx:
        app.batch_finish_mode(ws["input"], ws["output"])
    assert "진행 중" in str(ctx.value)

    await app.cancel_batch_async(ws["input"])
    with pytest.raises(BtgServiceException):
        app.batch_finish_mode(ws["input"], other)
    assert app.batch_finish_mode(ws["input"], ws["output"]) == "integrity"


@pytest.mark.asyncio
async def test_layout_change_after_submit_blocks_collection(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app = _make_app(ws, server)
    units = _units(app, ws)
    await app.start_translation_async(ws["input"], ws["output"])
    server.complete(server.only_job(), lambda idx, req: _json_reply(idx, units))

    app.config["integrity_max_items"] = 2
    with pytest.raises(BtgServiceException) as ctx:
        await app.refresh_batch_async(ws["input"], ws["output"])
    assert "되돌린" in str(ctx.value)

    app.config["integrity_max_items"] = 3
    assert (await app.refresh_batch_async(ws["input"], ws["output"])).complete


def test_integrity_request_converts_with_sdk(integrity_workspace):
    """배치 요청이 SDK 변환을 거쳐 responseSchema(ARRAY)로 나가는지 (실제 전송 직전 단계)"""
    from google.genai import _api_client, batches

    ws = integrity_workspace
    app = _make_app(ws, FakeBatchServer())
    units = _units(app, ws)
    req = app._get_batch_service()._build_inlined_request(0, units[0], {}, "integrity")
    body = batches._InlinedRequest_to_mldev(_api_client.BaseApiClient(api_key="dummy"), req)
    config = body["request"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    schema = config["responseSchema"]
    assert schema.type == t.Type.ARRAY and set(schema.items.properties) == {"id", "translated_text"}
    assert body["metadata"] == {"key": "chunk-00000"}


# ---------------------------------------------------------------------------
# 번역 기억: 배치 결과는 예시로 기록만 하고 인물 메모는 추출하지 않는다
# ---------------------------------------------------------------------------

class RecordingExtractor:
    def __init__(self):
        self.calls: List[str] = []

    async def extract(self, source, translation, known):
        self.calls.append(source)
        return []


def _make_memory_app(workspace, server):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "test_domain"))
    from test_translation_memory import FakeEmbedder

    cfg = json.loads(workspace["config"].read_text(encoding="utf-8"))
    cfg.update({"enable_translation_memory": True, "voyage_api_key": "vk", "enable_memory_extraction": True})
    workspace["config"].write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    app = _make_app(workspace, server)
    app.embedding_client_factory = lambda cfg: FakeEmbedder()
    extractor = RecordingExtractor()
    app.memory_extractor_factory = lambda cfg: extractor
    return app, extractor


@pytest.mark.asyncio
async def test_batch_collection_records_memory_without_extraction(workspace):
    server = FakeBatchServer()
    app, extractor = _make_memory_app(workspace, server)
    await app.start_translation_async(workspace["input"], workspace["output"])
    name = server.only_job()
    total = len(server.jobs[name]["requests"])

    def responder(idx, req):
        if idx == 1:
            return BatchItemResult(key=chunk_key(idx), blocked=True, finish_reason="PROHIBITED_CONTENT", error="응답 차단")
        return _translate_all(idx, req)

    server.complete(name, responder)
    await app.refresh_batch_async(workspace["input"], workspace["output"])
    assert app.translation_service.translation_memory.summary()["translated"] == total - 1
    assert extractor.calls == [] and not app._memory_tasks

    # 남은 청크는 실시간 마무리가 번역하면서 추출한다
    app.gemini_client.generate_text_async = AsyncMock(return_value="실시간 번역1")
    await app.start_translation_async(workspace["input"], workspace["output"], translation_mode_override="standard")
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_integrity_batch_collection_records_memory_without_extraction(integrity_workspace):
    ws = integrity_workspace
    server = FakeBatchServer()
    app, extractor = _make_memory_app(ws, server)
    units = _units(app, ws)
    await app.start_translation_async(ws["input"], ws["output"])
    server.complete(server.only_job(), lambda idx, req: _json_reply(idx, units))

    assert (await app.refresh_batch_async(ws["input"], ws["output"])).complete
    assert app.translation_service.translation_memory.summary()["translated"] > 0
    assert extractor.calls == [] and not app._memory_tasks
