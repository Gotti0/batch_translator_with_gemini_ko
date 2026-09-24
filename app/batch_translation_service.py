"""
Batch Translation Service for Neo Batch Translator (BTG)

Gemini Batch API로 표준 모드의 청크 번역을 제출하고, 나중에 결과를 수거합니다.

- 요청은 실시간 번역과 같은 빌더(TranslationService.build_translation_request,
  GeminiClient.build_generate_config)로 만든다.
- 결과는 표준 모드와 같은 청크 백업 파일과 메타데이터(`translated_chunks`)에 쓴다.
  그래서 배치에서 빠진 청크는 표준 모드 이어하기가 그대로 집어 실시간으로 마무리할 수 있다.
- 제출한 작업은 메타데이터의 `batch` 항목에 저장해 앱을 다시 켜도 이어서 조회한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from google.genai import types as genai_types

from core.exceptions import BtgServiceException
from infrastructure.file_handler import (
    _hash_config_for_metadata,
    create_new_metadata,
    delete_file,
    get_metadata_file_path,
    load_metadata,
    read_text_file,
    save_chunk_with_index_to_file,
    save_metadata,
    update_metadata_for_chunk_completion,
    update_metadata_for_chunk_failure,
)
from infrastructure.gemini_batch_client import (
    BatchItemResult,
    BatchJobInfo,
    GeminiBatchClient,
    key_fingerprint,
)
from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 18_000_000  # 인라인 요청 한도 20MB에 여유를 둔 값
# 제출 응답을 받기 전에 앱이 꺼진 작업을 이름으로 찾지 못하면, 이 시간이 지난 뒤 유실로 본다
LOST_JOB_GRACE_SECONDS = 600
BLOCKED_PREFIX = "[배치] 검열:"
ERROR_PREFIX = "[배치] 오류:"


@dataclass
class BatchSummary:
    total_chunks: int  # 번역 대상(공백이 아닌) 청크 수
    translated: int
    remaining: List[int]
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    round: int = 0
    blocked: int = 0
    errored: int = 0
    config_changed: bool = False

    @property
    def active_jobs(self) -> List[Dict[str, Any]]:
        return [j for j in self.jobs if not j.get("collected")]

    @property
    def active(self) -> bool:
        return bool(self.active_jobs)

    @property
    def complete(self) -> bool:
        return not self.active and not self.remaining

    def describe(self) -> str:
        if self.active:
            states = ", ".join(sorted({str(j.get("state", "?")) for j in self.active_jobs}))
            return f"배치 진행 중: 작업 {len(self.active_jobs)}개 ({states}) · 완료 {self.translated}/{self.total_chunks}"
        if self.remaining:
            detail = []
            if self.blocked:
                detail.append(f"검열 {self.blocked}")
            if self.errored:
                detail.append(f"오류 {self.errored}")
            extra = f" ({', '.join(detail)})" if detail else ""
            return f"배치 수거 완료: {self.translated}/{self.total_chunks} · 미완료 {len(self.remaining)}개{extra}"
        return f"배치 번역 완료: {self.translated}/{self.total_chunks}"


def chunk_key(index: int) -> str:
    return f"chunk-{index:05d}"


def parse_chunk_key(key: Optional[str]) -> Optional[int]:
    if not key:
        return None
    m = re.fullmatch(r"chunk-(\d+)", key)
    return int(m.group(1)) if m else None


def estimate_request_bytes(request: genai_types.InlinedRequest) -> int:
    """인라인 요청의 직렬화 크기 추정 (JSON, bytes는 base64)."""
    return len(request.model_dump_json(exclude_none=True).encode("utf-8"))


def group_requests_by_size(
    sized: List[Tuple[int, genai_types.InlinedRequest, int]], max_bytes: int
) -> Tuple[List[List[Tuple[int, genai_types.InlinedRequest]]], List[int]]:
    """청크 순서를 지키며 작업당 `max_bytes` 이하로 묶는다. 단독으로도 넘는 청크는 따로 돌려준다."""
    groups: List[List[Tuple[int, genai_types.InlinedRequest]]] = []
    oversized: List[int] = []
    current: List[Tuple[int, genai_types.InlinedRequest]] = []
    current_bytes = 0
    for idx, req, size in sized:
        if size > max_bytes:
            oversized.append(idx)
            continue
        if current and current_bytes + size > max_bytes:
            groups.append(current)
            current, current_bytes = [], 0
        current.append((idx, req))
        current_bytes += size
    if current:
        groups.append(current)
    return groups, oversized


class BatchTranslationService:
    """표준 모드 청크 번역을 Gemini Batch API로 제출·수거한다."""

    def __init__(
        self,
        config: Dict[str, Any],
        translation_service: Any,
        gemini_client: GeminiClient,
        chunk_service: Any,
        batch_client_factory: Optional[Callable[[str], GeminiBatchClient]] = None,
    ) -> None:
        self.config = config
        self.translation_service = translation_service
        self.gemini_client = gemini_client
        self.chunk_service = chunk_service
        self._batch_client_factory = batch_client_factory or (lambda key: GeminiBatchClient(key))
        # 청크 하나가 번역되어 수거될 때 호출 (idx, 원문, 번역문). 번역 기억 기록에 쓴다.
        self.on_chunk_translated: Optional[Callable[[int, str, str], None]] = None

    # ------------------------------------------------------------------
    # 경로·설정
    # ------------------------------------------------------------------

    @staticmethod
    def chunked_output_path(input_file_path: Path) -> Path:
        # 표준 모드와 같은 백업 파일 (AppService._do_translation_async와 동일한 규칙)
        return input_file_path.parent / f"{input_file_path.stem}_translated_chunked.txt"

    def _batch_key(self) -> Optional[str]:
        key = self.config.get("batch_api_key")
        return key.strip() if isinstance(key, str) and key.strip() else None

    def _api_keys(self) -> List[str]:
        """제출·조회에 쓸 수 있는 키 (배치 전용 키가 있으면 맨 앞)."""
        keys = self._rotation_keys()
        batch_key = self._batch_key()
        if batch_key:
            keys = [batch_key] + [k for k in keys if k != batch_key]
        return keys

    def _rotation_keys(self) -> List[str]:
        keys = self.config.get("api_keys") or []
        if isinstance(keys, str):
            keys = [keys]
        keys = [k.strip() for k in keys if isinstance(k, str) and k.strip()]
        single = self.config.get("api_key")
        if not keys and isinstance(single, str) and single.strip():
            keys = [single.strip()]
        return keys

    def check_available(self) -> Optional[str]:
        """배치 모드를 쓸 수 없는 이유를 돌려준다. 쓸 수 있으면 None."""
        if str(self.config.get("llm_provider", "gemini")).lower() != "gemini":
            return "배치 번역은 Google Gemini API 프로바이더에서만 사용할 수 있습니다."
        if self.config.get("use_vertex_ai"):
            return "배치 번역은 Vertex AI를 지원하지 않습니다. Gemini API 키를 사용하세요."
        if not self._api_keys():
            return "배치 번역에는 Gemini API 키가 필요합니다."
        return None

    def _client_for_submit(self) -> Tuple[GeminiBatchClient, str]:
        reason = self.check_available()
        if reason:
            raise BtgServiceException(reason)
        # 무료 티어 키는 Batch API를 쓸 수 없으므로 배치 전용(유료) 키를 우선한다. 없으면 첫 키.
        # 작업은 제출한 키의 프로젝트에만 보이므로 조회도 이 키로 한다.
        key = self._api_keys()[0]
        return self._batch_client_factory(key), key_fingerprint(key)

    def _client_for_fingerprint(self, fingerprint: str) -> GeminiBatchClient:
        for key in self._api_keys():
            if key_fingerprint(key) == fingerprint:
                return self._batch_client_factory(key)
        raise BtgServiceException(
            "배치 작업을 제출한 API 키가 설정에 없습니다. 제출 때 쓴 키를 API 키 목록에 다시 추가하세요 "
            "(결과는 Google에 6주간 보관됩니다)."
        )

    def _request_signature(self) -> str:
        """제출 뒤 번역 설정이 바뀌었는지 알리기 위한 해시 (결과 수거에는 영향 없음)."""
        keys = [
            "model_name", "prompts", "temperature", "top_p", "thinking_level", "thinking_budget",
            "enable_prefill_translation", "prefill_system_instruction", "prefill_cached_history",
            "enable_dynamic_glossary_injection", "glossary_json_path", "enable_pagefold", "pagefold_mode",
        ]
        payload = json.dumps({k: self.config.get(k) for k in keys}, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # 메타데이터
    # ------------------------------------------------------------------

    def _load_chunks(self, input_file_path: Path) -> List[str]:
        content = read_text_file(input_file_path)
        return self.chunk_service.create_chunks_from_file_content(content, self.config.get("chunk_size", 6000))

    def _prepare_metadata(self, input_file_path: Path, total_chunks: int) -> Dict[str, Any]:
        """표준 모드와 같은 규칙으로 메타데이터를 이어 쓰거나 새로 만든다."""
        metadata = load_metadata(input_file_path) or {}
        if metadata.get("config_hash") == _hash_config_for_metadata(self.config) and metadata.get("total_chunks") == total_chunks:
            return metadata
        if metadata.get("batch") and any(not j.get("collected") for j in metadata["batch"].get("jobs", [])):
            raise BtgServiceException(
                "청크 크기나 원문이 바뀌었지만 수거하지 않은 배치 작업이 있습니다. 먼저 작업을 취소하거나 수거하세요."
            )
        logger.info("배치 번역: 새 메타데이터를 만들고 청크 백업 파일을 초기화합니다.")
        metadata = create_new_metadata(input_file_path, total_chunks, self.config)
        chunked = self.chunked_output_path(input_file_path)
        delete_file(chunked)
        chunked.touch()
        save_metadata(input_file_path, metadata)
        return metadata

    def _save_batch_section(self, input_file_path: Path, batch: Dict[str, Any], status: Optional[str] = None) -> None:
        # 청크 완료/실패 기록은 파일을 직접 읽고 쓰므로, batch 항목은 항상 최신 파일에 덮어 쓴다
        metadata = load_metadata(input_file_path) or {}
        metadata["batch"] = batch
        metadata["pipeline_type"] = "batch"
        if status:
            metadata["status"] = status
        metadata["last_updated"] = time.time()
        save_metadata(input_file_path, metadata)

    def summarize(self, input_file_path: Path, chunks: Optional[List[str]] = None) -> BatchSummary:
        input_file_path = Path(input_file_path)
        chunks = chunks if chunks is not None else self._load_chunks(input_file_path)
        metadata = load_metadata(input_file_path) or {}
        batch = metadata.get("batch") or {}
        translated = metadata.get("translated_chunks") or {}
        failed = metadata.get("failed_chunks") or {}
        same_layout = (
            metadata.get("config_hash") == _hash_config_for_metadata(self.config)
            and metadata.get("total_chunks") == len(chunks)
        )
        if not same_layout:
            translated, failed = {}, {}
        remaining = [i for i, c in enumerate(chunks) if c.strip() and str(i) not in translated]
        blocked = sum(1 for i in remaining if str(failed.get(str(i), {}).get("error", "")).startswith(BLOCKED_PREFIX))
        errored = sum(1 for i in remaining if str(failed.get(str(i), {}).get("error", "")).startswith(ERROR_PREFIX))
        # 공백뿐인 청크는 번역하지 않으므로(표준 모드와 동일) 진행률 분모에서 뺀다
        translatable = [i for i, c in enumerate(chunks) if c.strip()]
        return BatchSummary(
            total_chunks=len(translatable),
            translated=sum(1 for i in translatable if str(i) in translated),
            remaining=remaining,
            jobs=list(batch.get("jobs") or []),
            round=int(batch.get("round") or 0),
            blocked=blocked,
            errored=errored,
            config_changed=bool(batch.get("request_signature")) and batch.get("request_signature") != self._request_signature(),
        )

    # ------------------------------------------------------------------
    # 제출
    # ------------------------------------------------------------------

    def _build_inlined_request(self, index: int, text: str, uploaded: Dict[int, genai_types.Part]) -> genai_types.InlinedRequest:
        request = self.translation_service.build_translation_request(text)
        parts = [uploaded.get(id(p), p) for p in (request.multimodal_parts or [])] or None
        model = self.config.get("model_name", "gemini-2.0-flash")
        config = self.gemini_client.build_generate_config(
            model,
            self.translation_service.build_generation_config_dict(),
            thinking_budget=self.config.get("thinking_budget"),
            system_instruction_text=request.system_instruction,
            multimodal_parts=parts,
            for_batch=True,
        )
        return genai_types.InlinedRequest(
            contents=GeminiClient.build_sdk_contents(request.contents, parts),
            config=config,
            metadata={"key": chunk_key(index)},
        )

    async def _upload_shared_parts(
        self, client: GeminiBatchClient, texts: List[Tuple[int, str]], stem: str
    ) -> Tuple[Dict[int, genai_types.Part], List[str]]:
        """여러 청크가 함께 쓰는 인라인 파일(PageFold 용어집 PDF)을 한 번만 올리고 URI로 바꾼다."""
        seen: Dict[int, Tuple[genai_types.Part, int]] = {}
        for _, text in texts[:2]:  # 공유 파트는 세션 캐시 객체라 두 청크만 보면 된다
            for p in (self.translation_service.build_translation_request(text).multimodal_parts or []):
                if getattr(p, "inline_data", None) is not None:
                    part, count = seen.get(id(p), (p, 0))
                    seen[id(p)] = (part, count + 1)
        replacements: Dict[int, genai_types.Part] = {}
        uploaded_names: List[str] = []
        for pid, (part, count) in seen.items():
            if count < 2:  # 한 청크만 쓰는 파트(<pdf> 태그 PDF 등)는 인라인으로 둔다
                continue
            blob = part.inline_data
            file = await client.upload_file(blob.data, blob.mime_type or "application/pdf", f"btg-{stem}-shared")
            replacements[pid] = genai_types.Part.from_uri(file_uri=file.uri, mime_type=blob.mime_type or "application/pdf")
            uploaded_names.append(file.name)
        return replacements, uploaded_names

    async def submit_async(
        self,
        input_file_path: Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        input_file_path = Path(input_file_path)
        chunks = self._load_chunks(input_file_path)
        metadata = self._prepare_metadata(input_file_path, len(chunks))
        batch: Dict[str, Any] = dict(metadata.get("batch") or {})
        jobs: List[Dict[str, Any]] = list(batch.get("jobs") or [])
        if any(not j.get("collected") for j in jobs):
            raise BtgServiceException("이미 진행 중인 배치 작업이 있습니다. 상태를 확인하거나 취소한 뒤 다시 제출하세요.")

        translated = metadata.get("translated_chunks") or {}
        pending = [(i, c) for i, c in enumerate(chunks) if c.strip() and str(i) not in translated]
        if not pending:
            return self.summarize(input_file_path, chunks)

        client, fingerprint = self._client_for_submit()
        if batch.get("key_fingerprint") and batch["key_fingerprint"] != fingerprint and jobs:
            logger.info("배치 번역: 이전 라운드와 다른 키로 제출합니다.")
        model = self.config.get("model_name", "gemini-2.0-flash")
        stem = re.sub(r"[^0-9A-Za-z._-]+", "_", input_file_path.stem)[:40] or "input"
        token = hashlib.sha256(str(input_file_path.resolve()).encode("utf-8")).hexdigest()[:6]
        round_no = int(batch.get("round") or 0) + 1

        if status_callback:
            status_callback(f"배치 요청 준비 중 ({len(pending)}개 청크)...")
        replacements, uploaded = await self._upload_shared_parts(client, pending, stem)
        sized = []
        for i, text in pending:
            req = self._build_inlined_request(i, text, replacements)
            sized.append((i, req, estimate_request_bytes(req)))
        max_bytes = int(self.config.get("batch_max_request_bytes") or DEFAULT_MAX_REQUEST_BYTES)
        groups, oversized = group_requests_by_size(sized, max_bytes)
        for idx in oversized:
            update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} 요청이 배치 한도({max_bytes} bytes)보다 큽니다.")

        batch.update({
            "model": model,
            "key_fingerprint": fingerprint,
            "round": round_no,
            "request_signature": self._request_signature(),
            "uploaded_files": list(batch.get("uploaded_files") or []) + uploaded,
            "jobs": jobs,
        })

        for n, group in enumerate(groups, start=1):
            entry = {
                "display_name": f"btg-{stem}-{token}-r{round_no}-{n}",
                "name": None,
                "chunks": [idx for idx, _ in group],
                "state": "SUBMITTING",
                "submitted_at": time.time(),
                "collected": False,
            }
            jobs.append(entry)
            # 제출 전에 먼저 기록해 둔다. 응답을 받기 전에 앱이 꺼져도 display_name으로 찾을 수 있다.
            self._save_batch_section(input_file_path, batch, status="batch_submitted")
            if status_callback:
                status_callback(f"배치 작업 제출 중 ({n}/{len(groups)}, 청크 {len(group)}개)...")
            try:
                info = await client.submit(model, [req for _, req in group], entry["display_name"])
            except Exception as e:
                entry.update({"state": "SUBMIT_FAILED", "collected": True, "error": str(e)})
                self._save_batch_section(input_file_path, batch)
                raise
            entry.update({"name": info.name, "state": info.raw_state or info.state.value})
            self._save_batch_section(input_file_path, batch, status="batch_submitted")

        summary = self.summarize(input_file_path, chunks)
        if status_callback:
            status_callback(summary.describe())
        return summary

    # ------------------------------------------------------------------
    # 조회·수거
    # ------------------------------------------------------------------

    def _record_results(
        self,
        input_file_path: Path,
        chunks: List[str],
        job: Dict[str, Any],
        info: BatchJobInfo,
    ) -> None:
        chunked = self.chunked_output_path(input_file_path)
        translated = (load_metadata(input_file_path) or {}).get("translated_chunks") or {}
        job_chunks = set(job.get("chunks") or [])
        seen = set()
        ok = blocked = errored = 0

        for result in info.results or []:
            idx = parse_chunk_key(result.key)
            if idx is None or idx not in job_chunks or idx >= len(chunks):
                continue
            seen.add(idx)
            if str(idx) in translated:
                continue
            reason = self._record_one(input_file_path, chunked, idx, chunks[idx], result)
            if reason is None:
                ok += 1
            elif reason == "blocked":
                blocked += 1
            else:
                errored += 1

        missing_reason = info.error_message or f"작업 상태 {info.raw_state or info.state.value}"
        for idx in sorted(job_chunks - seen):
            if idx < len(chunks) and str(idx) not in translated:
                update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} 결과 없음 ({missing_reason})")
                errored += 1

        job.update({"collected": True, "succeeded": ok, "blocked": blocked, "errored": errored, "collected_at": time.time()})
        logger.info(f"배치 작업 수거: {job.get('name')} 성공 {ok}, 검열 {blocked}, 오류 {errored}")

    def _record_one(self, input_file_path: Path, chunked: Path, idx: int, source: str, result: BatchItemResult) -> Optional[str]:
        if result.text is not None:
            try:
                text = self.translation_service.finalize_translation_text(source, result.text)
            except GeminiContentSafetyException as e:
                update_metadata_for_chunk_failure(input_file_path, idx, f"{BLOCKED_PREFIX} {e}")
                return "blocked"
            save_chunk_with_index_to_file(chunked, idx, text)
            if self.on_chunk_translated is not None:
                self.on_chunk_translated(idx, source, text)  # 번역 기억 기록 (AppService)
            update_metadata_for_chunk_completion(input_file_path, idx, len(source), len(text))
            return None
        if result.blocked:
            update_metadata_for_chunk_failure(input_file_path, idx, f"{BLOCKED_PREFIX} {result.error or result.finish_reason}")
            return "blocked"
        update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} {result.error}")
        return "error"

    async def refresh_async(
        self,
        input_file_path: Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        """진행 중인 작업의 상태를 조회하고, 끝난 작업의 결과를 수거한다."""
        input_file_path = Path(input_file_path)
        chunks = self._load_chunks(input_file_path)
        metadata = load_metadata(input_file_path) or {}
        batch: Dict[str, Any] = dict(metadata.get("batch") or {})
        jobs = list(batch.get("jobs") or [])
        active = [j for j in jobs if not j.get("collected")]
        if not active:
            return self.summarize(input_file_path, chunks)

        client = self._client_for_fingerprint(batch.get("key_fingerprint", ""))
        batch["jobs"] = jobs
        for job in active:
            info: Optional[BatchJobInfo]
            if job.get("name"):
                info = await client.get(job["name"])
            else:
                info = await client.find_by_display_name(job["display_name"])
                if info is None:
                    if time.time() - float(job.get("submitted_at") or 0) > LOST_JOB_GRACE_SECONDS:
                        job.update({"state": "LOST", "collected": True})
                        logger.warning(f"제출이 확인되지 않은 배치 작업을 유실로 처리합니다: {job['display_name']}")
                    continue
                job["name"] = info.name
            job["state"] = info.raw_state or info.state.value
            if info.successful_count is not None:
                job["successful_count"] = info.successful_count
            if info.failed_count is not None:
                job["failed_count"] = info.failed_count
            if info.state.is_terminal:
                self._record_results(input_file_path, chunks, job, info)
            self._save_batch_section(input_file_path, batch)

        summary = self.summarize(input_file_path, chunks)
        self._save_batch_section(
            input_file_path, batch,
            status="batch_submitted" if summary.active else ("batch_collected" if summary.remaining else "batch_translated"),
        )
        if status_callback:
            status_callback(summary.describe())
        return summary

    async def cancel_async(
        self,
        input_file_path: Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        """진행 중인 작업을 취소하고, 이미 끝난 결과가 있으면 수거한다."""
        input_file_path = Path(input_file_path)
        metadata = load_metadata(input_file_path) or {}
        batch = metadata.get("batch") or {}
        active = [j for j in (batch.get("jobs") or []) if not j.get("collected") and j.get("name")]
        if active:
            client = self._client_for_fingerprint(batch.get("key_fingerprint", ""))
            for job in active:
                await client.cancel(job["name"])
        return await self.refresh_async(input_file_path, status_callback)

    def write_failure_placeholders(self, input_file_path: Path) -> int:
        """미완료 청크에 실시간 모드와 같은 실패 표시와 원문을 써서 최종 파일에 빠지지 않게 한다."""
        input_file_path = Path(input_file_path)
        chunks = self._load_chunks(input_file_path)
        summary = self.summarize(input_file_path, chunks)
        if summary.active:
            raise BtgServiceException("진행 중인 배치 작업이 있어 아직 저장할 수 없습니다.")
        failed = (load_metadata(input_file_path) or {}).get("failed_chunks") or {}
        chunked = self.chunked_output_path(input_file_path)
        for idx in summary.remaining:
            reason = failed.get(str(idx), {}).get("error", "배치 번역 결과 없음")
            save_chunk_with_index_to_file(chunked, idx, f"[번역 실패: {reason}]\n\n--- 원문 내용 ---\n{chunks[idx]}")
        return len(summary.remaining)

    @staticmethod
    def metadata_path(input_file_path: Path) -> Path:
        return get_metadata_file_path(input_file_path)
