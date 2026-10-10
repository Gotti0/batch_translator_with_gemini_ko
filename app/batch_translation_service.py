"""
Batch Translation Service for Neo Batch Translator (BTG)

Gemini Batch API로 청크 번역을 제출하고, 나중에 결과를 수거합니다.
번역 방식(pipeline)은 일반(standard)과 무결성(integrity) 중에서 고릅니다.

- 요청은 실시간 번역과 같은 빌더로 만든다. 일반은 TranslationService.build_translation_request,
  무결성은 build_integrity_request(줄 ID JSON과 응답 스키마)다.
- 결과는 실시간 모드와 같은 저장소에 쓴다. 일반은 청크 백업 파일과 메타데이터(`translated_chunks`),
  무결성은 출력 경로 옆 임시 폴더의 chunk_<i>.json이다. 그래서 배치에서 빠진 청크는 같은 방식의
  실시간 이어하기가 그대로 집어 마무리할 수 있다.
- 제출한 작업은 메타데이터의 `batch` 항목에 저장해 앱을 다시 켜도 이어서 조회한다. 번역 방식도 여기에
  남는다. 진행 중인 배치의 조회·수거·마무리는 설정이 아니라 제출 당시의 방식을 따른다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from google.genai import types as genai_types

from core.dtos import TranslationUnit
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
    to_serializable_response_schema,
)
from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException
from core.exceptions import BtgApiContentSafetyException
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 18_000_000  # 인라인 요청 한도 20MB에 여유를 둔 값
# 제출 응답을 받기 전에 앱이 꺼진 작업을 이름으로 찾지 못하면, 이 시간이 지난 뒤 유실로 본다
LOST_JOB_GRACE_SECONDS = 600
BLOCKED_PREFIX = "[배치] 검열:"
ERROR_PREFIX = "[배치] 오류:"
PARTIAL_PREFIX = "[배치] 누락:"

PIPELINE_STANDARD = "standard"
PIPELINE_INTEGRITY = "integrity"
PIPELINE_LABELS = {PIPELINE_STANDARD: "일반", PIPELINE_INTEGRITY: "무결성"}

ChunkItem = Union[str, List[TranslationUnit]]


def normalize_pipeline(value: Any) -> str:
    return PIPELINE_INTEGRITY if str(value or "").strip().lower() == PIPELINE_INTEGRITY else PIPELINE_STANDARD


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
    pipeline: str = PIPELINE_STANDARD
    partial: int = 0  # 무결성: 일부 줄만 받아 실시간 마무리가 빠진 줄만 묻게 될 청크

    @property
    def active_jobs(self) -> List[Dict[str, Any]]:
        return [j for j in self.jobs if not j.get("collected")]

    @property
    def active(self) -> bool:
        return bool(self.active_jobs)

    @property
    def complete(self) -> bool:
        return not self.active and not self.remaining

    @property
    def pipeline_label(self) -> str:
        return PIPELINE_LABELS[self.pipeline]

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
            if self.partial:
                detail.append(f"누락 {self.partial}")
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
    """청크 번역을 Gemini Batch API로 제출·수거한다 (일반·무결성)."""

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
        # 청크 하나가 번역되어 수거될 때 호출. 번역 기억 기록에 쓴다.
        # 일반: (idx, 원문, 번역문), 무결성: (idx, 단위 목록, 줄 ID → 번역문)
        self.on_chunk_translated: Optional[Callable[[int, str, str], None]] = None
        self.on_integrity_chunk_translated: Optional[Callable[[int, List[TranslationUnit], Dict[str, str]], None]] = None

    # ------------------------------------------------------------------
    # 경로·설정
    # ------------------------------------------------------------------

    @staticmethod
    def chunked_output_path(input_file_path: Path) -> Path:
        # 표준 모드와 같은 백업 파일 (AppService._do_translation_async와 동일한 규칙)
        return input_file_path.parent / f"{input_file_path.stem}_translated_chunked.txt"

    @staticmethod
    def default_output_path(input_file_path: Path) -> Path:
        # GUI·CLI가 출력 경로를 비웠을 때 쓰는 기본값과 같다
        return input_file_path.parent / f"{input_file_path.stem}_translated{input_file_path.suffix}"

    def configured_pipeline(self) -> str:
        """다음에 새로 제출할 때 쓸 번역 방식 (설정값)."""
        return normalize_pipeline(self.config.get("batch_pipeline"))

    def _session_pipeline(self, batch: Dict[str, Any]) -> str:
        # 배치 항목에 기록된 방식. 방식을 기록하기 전의 메타데이터는 모두 일반 방식이었다.
        if batch.get("pipeline"):
            return normalize_pipeline(batch["pipeline"])
        if batch.get("jobs"):
            return PIPELINE_STANDARD
        return self.configured_pipeline()

    def session_pipeline(self, input_file_path: Path) -> str:
        """진행 중이거나 마지막으로 제출한 배치의 번역 방식."""
        return self._session_pipeline((load_metadata(input_file_path) or {}).get("batch") or {})

    def stored_output_path(self, input_file_path: Path) -> Optional[Path]:
        """무결성 배치가 결과를 모으는 출력 경로 (제출 때 기록). 일반 배치나 기록이 없으면 None."""
        batch = (load_metadata(input_file_path) or {}).get("batch") or {}
        if self._session_pipeline(batch) != PIPELINE_INTEGRITY or not batch.get("output_path"):
            return None
        return Path(batch["output_path"])

    def _output_path(self, input_file_path: Path, batch: Dict[str, Any], output_path: Optional[Path] = None) -> Path:
        if output_path:
            return Path(output_path)
        if batch.get("output_path"):
            return Path(batch["output_path"])
        return self.default_output_path(input_file_path)

    def integrity_temp_dir(self, input_file_path: Path, output_path: Optional[Path] = None) -> Path:
        """무결성 결과 폴더. 출력 경로를 넘기지 않으면 제출 때 기록한 경로 기준이다."""
        batch = (load_metadata(input_file_path) or {}).get("batch") or {}
        return self.translation_service.integrity_temp_dir_for(self._output_path(Path(input_file_path), batch, output_path))

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
    # 청크
    # ------------------------------------------------------------------

    def _load_chunks(self, input_file_path: Path) -> List[str]:
        content = read_text_file(input_file_path)
        return self.chunk_service.create_chunks_from_file_content(content, self.config.get("chunk_size", 6000))

    def load_units(self, input_file_path: Path) -> List[List[TranslationUnit]]:
        """무결성 청크 (실시간 무결성 모드와 같은 경계)."""
        return self.translation_service.split_integrity_chunks(read_text_file(input_file_path))

    def _load_items(self, input_file_path: Path, pipeline: str) -> List[ChunkItem]:
        if pipeline == PIPELINE_INTEGRITY:
            return list(self.load_units(input_file_path))
        return list(self._load_chunks(input_file_path))

    @staticmethod
    def _translatable(item: ChunkItem) -> bool:
        # 공백뿐인 청크는 번역하지 않는다 (표준 모드와 동일)
        if isinstance(item, str):
            return bool(item.strip())
        return any(u.text.strip() for u in item)

    def _item_text(self, item: ChunkItem) -> str:
        return item if isinstance(item, str) else self.translation_service.integrity_chunk_text(item)

    def _layout_signature(self, pipeline: str, items: List[ChunkItem]) -> str:
        """청크 경계와 원문의 지문. 제출 뒤 청크 크기·최대 항목 수·원문이 바뀌면 달라진다."""
        payload = json.dumps([pipeline, [self._item_text(i) for i in items]], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # 메타데이터
    # ------------------------------------------------------------------

    def _standard_layout_matches(self, metadata: Dict[str, Any], total_chunks: int) -> bool:
        # 무결성 배치가 남긴 메타데이터는 청크 번호가 다른 경계를 가리키므로 이어 쓰지 않는다
        return (
            metadata.get("pipeline_type") != PIPELINE_INTEGRITY
            and metadata.get("config_hash") == _hash_config_for_metadata(self.config)
            and metadata.get("total_chunks") == total_chunks
        )

    def _prepare_metadata(self, input_file_path: Path, total_chunks: int) -> Dict[str, Any]:
        """표준 모드와 같은 규칙으로 메타데이터를 이어 쓰거나 새로 만든다."""
        metadata = load_metadata(input_file_path) or {}
        if self._standard_layout_matches(metadata, total_chunks):
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

    def _integrity_completed(self, temp_dir: Path, units: List[List[TranslationUnit]]) -> Set[int]:
        load = self.translation_service.load_integrity_chunk_result
        return {i for i, chunk in enumerate(units) if load(temp_dir, i, chunk) is not None}

    def _prepare_integrity_metadata(
        self, input_file_path: Path, units: List[List[TranslationUnit]], temp_dir: Path
    ) -> Dict[str, Any]:
        """무결성 배치의 메타데이터를 임시 폴더의 완료 상태에 맞춘다.

        완료 기준은 실시간 무결성 모드처럼 임시 폴더의 청크 파일이고, 메타데이터는 그 사본이다.
        다른 방식이 남긴 실패 기록은 청크 번호가 맞지 않으므로 버린다.
        """
        metadata = load_metadata(input_file_path) or {}
        same = metadata.get("pipeline_type") == PIPELINE_INTEGRITY and metadata.get("total_chunks") == len(units)
        completed = self._integrity_completed(temp_dir, units)
        previous = (metadata.get("translated_chunks") or {}) if same else {}
        failed = (metadata.get("failed_chunks") or {}) if same else {}
        now = time.time()
        metadata.update({
            "input_file": str(input_file_path),
            "pipeline_type": PIPELINE_INTEGRITY,
            "total_chunks": len(units),
            "translated_chunks": {str(i): previous.get(str(i)) or {"status": "success"} for i in sorted(completed)},
            "failed_chunks": {k: v for k, v in failed.items() if k not in {str(i) for i in completed}},
            "last_updated": now,
        })
        metadata.setdefault("creation_time", now)
        metadata.setdefault("status", "initialized")
        # 표준 경로가 이 메타데이터를 자기 진행 상황으로 이어받지 않게 한다 (청크 번호의 뜻이 다르다)
        metadata.pop("config_hash", None)
        save_metadata(input_file_path, metadata)
        return metadata

    def _save_batch_section(self, input_file_path: Path, batch: Dict[str, Any], status: Optional[str] = None) -> None:
        # 청크 완료/실패 기록은 파일을 직접 읽고 쓰므로, batch 항목은 항상 최신 파일에 덮어 쓴다
        metadata = load_metadata(input_file_path) or {}
        metadata["batch"] = batch
        # 검토 탭은 이 값으로 화면을 고른다. 무결성 배치는 무결성 검토 화면(임시 폴더)을 쓴다.
        metadata["pipeline_type"] = PIPELINE_INTEGRITY if batch.get("pipeline") == PIPELINE_INTEGRITY else "batch"
        if status:
            metadata["status"] = status
        metadata["last_updated"] = time.time()
        save_metadata(input_file_path, metadata)

    def _completed_indices(
        self, input_file_path: Path, pipeline: str, items: List[ChunkItem], metadata: Dict[str, Any], batch: Dict[str, Any]
    ) -> Set[int]:
        if pipeline == PIPELINE_INTEGRITY:
            temp_dir = self.translation_service.integrity_temp_dir_for(self._output_path(input_file_path, batch))
            return self._integrity_completed(temp_dir, items)  # type: ignore[arg-type]
        if not self._standard_layout_matches(metadata, len(items)):
            return set()
        translated = metadata.get("translated_chunks") or {}
        return {i for i in range(len(items)) if str(i) in translated}

    def summarize(self, input_file_path: Path, chunks: Optional[List[ChunkItem]] = None) -> BatchSummary:
        input_file_path = Path(input_file_path)
        metadata = load_metadata(input_file_path) or {}
        batch = metadata.get("batch") or {}
        pipeline = self._session_pipeline(batch)
        items = chunks if chunks is not None else self._load_items(input_file_path, pipeline)
        completed = self._completed_indices(input_file_path, pipeline, items, metadata, batch)
        if pipeline == PIPELINE_INTEGRITY:
            same_layout = metadata.get("pipeline_type") == PIPELINE_INTEGRITY
        else:
            same_layout = self._standard_layout_matches(metadata, len(items))
        failed = (metadata.get("failed_chunks") or {}) if same_layout else {}
        translatable = [i for i, c in enumerate(items) if self._translatable(c)]
        remaining = [i for i in translatable if i not in completed]

        def count(prefix: str) -> int:
            return sum(1 for i in remaining if str(failed.get(str(i), {}).get("error", "")).startswith(prefix))

        return BatchSummary(
            total_chunks=len(translatable),
            translated=sum(1 for i in translatable if i in completed),
            remaining=remaining,
            jobs=list(batch.get("jobs") or []),
            round=int(batch.get("round") or 0),
            blocked=count(BLOCKED_PREFIX),
            errored=count(ERROR_PREFIX),
            partial=count(PARTIAL_PREFIX),
            config_changed=bool(batch.get("request_signature")) and batch.get("request_signature") != self._request_signature(),
            pipeline=pipeline,
        )

    # ------------------------------------------------------------------
    # 제출
    # ------------------------------------------------------------------

    def _build_request_parts(self, pipeline: str, item: ChunkItem) -> Tuple[Any, Dict[str, Any]]:
        ts = self.translation_service
        if pipeline == PIPELINE_INTEGRITY:
            return ts.build_integrity_request(item), ts.build_integrity_generation_config_dict()
        return ts.build_translation_request(item), ts.build_generation_config_dict()

    def _build_inlined_request(
        self, index: int, item: ChunkItem, uploaded: Dict[int, genai_types.Part], pipeline: str = PIPELINE_STANDARD
    ) -> genai_types.InlinedRequest:
        request, generation_config = self._build_request_parts(pipeline, item)
        parts = [uploaded.get(id(p), p) for p in (request.multimodal_parts or [])] or None
        model = self.config.get("model_name", "gemini-2.0-flash")
        config = self.gemini_client.build_generate_config(
            model,
            generation_config,
            thinking_budget=self.config.get("thinking_budget"),
            system_instruction_text=request.system_instruction,
            multimodal_parts=parts,
            for_batch=True,
        )
        if config.response_schema is not None:
            config = config.model_copy(update={"response_schema": to_serializable_response_schema(config.response_schema)})
        return genai_types.InlinedRequest(
            contents=GeminiClient.build_sdk_contents(request.contents, parts),
            config=config,
            metadata={"key": chunk_key(index)},
        )

    async def _upload_shared_parts(
        self, client: GeminiBatchClient, items: List[Tuple[int, ChunkItem]], stem: str, pipeline: str = PIPELINE_STANDARD
    ) -> Tuple[Dict[int, genai_types.Part], List[str]]:
        """여러 청크가 함께 쓰는 인라인 파일(PageFold 용어집 PDF)을 한 번만 올리고 URI로 바꾼다."""
        seen: Dict[int, Tuple[genai_types.Part, int]] = {}
        for _, item in items[:2]:  # 공유 파트는 세션 캐시 객체라 두 청크만 보면 된다
            for p in (self._build_request_parts(pipeline, item)[0].multimodal_parts or []):
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
        output_path: Optional[Path] = None,
        pipeline: Optional[str] = None,
    ) -> BatchSummary:
        """미번역 청크를 제출한다.

        `pipeline`을 주지 않으면 설정값을 쓴다. 마지막 배치와 방식이 다르면 새 배치로 시작한다(이전 작업
        목록은 다른 청크 경계를 가리키므로 버린다). 무결성 방식은 `output_path` 기준 임시 폴더에 결과를 모으며,
        이 경로를 배치 항목에 기록해 수거·마무리가 같은 폴더를 쓰게 한다.
        """
        input_file_path = Path(input_file_path)
        pipeline = normalize_pipeline(pipeline) if pipeline else self.configured_pipeline()
        metadata = load_metadata(input_file_path) or {}
        batch: Dict[str, Any] = dict(metadata.get("batch") or {})
        jobs: List[Dict[str, Any]] = list(batch.get("jobs") or [])
        if any(not j.get("collected") for j in jobs):
            raise BtgServiceException("이미 진행 중인 배치 작업이 있습니다. 상태를 확인하거나 취소한 뒤 다시 제출하세요.")
        if batch and self._session_pipeline(batch) != pipeline:
            logger.info(
                f"배치 번역: {PIPELINE_LABELS[self._session_pipeline(batch)]} 방식 대신 "
                f"{PIPELINE_LABELS[pipeline]} 방식으로 새 배치를 시작합니다."
            )
            batch, jobs = {}, []

        items = self._load_items(input_file_path, pipeline)
        if pipeline == PIPELINE_INTEGRITY:
            out = self._output_path(input_file_path, batch, output_path)
            batch["output_path"] = str(out)
            temp_dir = self.translation_service.integrity_temp_dir_for(out)
            temp_dir.mkdir(parents=True, exist_ok=True)
            self._prepare_integrity_metadata(input_file_path, items, temp_dir)  # type: ignore[arg-type]
        else:
            batch.pop("output_path", None)
            self._prepare_metadata(input_file_path, len(items))
        metadata = load_metadata(input_file_path) or {}
        completed = self._completed_indices(input_file_path, pipeline, items, metadata, batch)
        pending = [(i, c) for i, c in enumerate(items) if self._translatable(c) and i not in completed]

        batch.update({
            "pipeline": pipeline,
            "layout": self._layout_signature(pipeline, items),
            "jobs": jobs,
        })
        if not pending:
            self._save_batch_section(input_file_path, batch)
            return self.summarize(input_file_path, items)

        client, fingerprint = self._client_for_submit()
        if batch.get("key_fingerprint") and batch["key_fingerprint"] != fingerprint and jobs:
            logger.info("배치 번역: 이전 라운드와 다른 키로 제출합니다.")
        model = self.config.get("model_name", "gemini-2.0-flash")
        stem = re.sub(r"[^0-9A-Za-z._-]+", "_", input_file_path.stem)[:40] or "input"
        token = hashlib.sha256(str(input_file_path.resolve()).encode("utf-8")).hexdigest()[:6]
        round_no = int(batch.get("round") or 0) + 1

        if status_callback:
            status_callback(f"배치 요청 준비 중 ({PIPELINE_LABELS[pipeline]} 방식, {len(pending)}개 청크)...")
        replacements, uploaded = await self._upload_shared_parts(client, pending, stem, pipeline)
        sized = []
        for i, item in pending:
            req = self._build_inlined_request(i, item, replacements, pipeline)
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

        summary = self.summarize(input_file_path, items)
        if status_callback:
            status_callback(summary.describe())
        return summary

    # ------------------------------------------------------------------
    # 조회·수거
    # ------------------------------------------------------------------

    def _record_results(
        self,
        input_file_path: Path,
        pipeline: str,
        items: List[ChunkItem],
        job: Dict[str, Any],
        info: BatchJobInfo,
        batch: Dict[str, Any],
    ) -> None:
        metadata = load_metadata(input_file_path) or {}
        completed = self._completed_indices(input_file_path, pipeline, items, metadata, batch)
        temp_dir = (
            self.translation_service.integrity_temp_dir_for(self._output_path(input_file_path, batch))
            if pipeline == PIPELINE_INTEGRITY else None
        )
        job_chunks = set(job.get("chunks") or [])
        seen = set()
        counts = {"ok": 0, "blocked": 0, "error": 0, "partial": 0}

        for result in info.results or []:
            idx = parse_chunk_key(result.key)
            if idx is None or idx not in job_chunks or idx >= len(items):
                continue
            seen.add(idx)
            if idx in completed:
                continue
            if temp_dir is not None:
                outcome = self._record_integrity_one(input_file_path, temp_dir, idx, items[idx], result)  # type: ignore[arg-type]
            else:
                outcome = self._record_one(input_file_path, self.chunked_output_path(input_file_path), idx, items[idx], result)  # type: ignore[arg-type]
            counts[outcome or "ok"] += 1

        missing_reason = info.error_message or f"작업 상태 {info.raw_state or info.state.value}"
        for idx in sorted(job_chunks - seen):
            if idx < len(items) and idx not in completed:
                update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} 결과 없음 ({missing_reason})")
                counts["error"] += 1

        job.update({
            "collected": True, "succeeded": counts["ok"], "blocked": counts["blocked"], "errored": counts["error"],
            "collected_at": time.time(),
        })
        if counts["partial"]:
            job["partial"] = counts["partial"]
        logger.info(
            f"배치 작업 수거: {job.get('name')} 성공 {counts['ok']}, 검열 {counts['blocked']}, "
            f"오류 {counts['error']}, 누락 {counts['partial']}"
        )

    def _record_one(self, input_file_path: Path, chunked: Path, idx: int, source: str, result: BatchItemResult) -> Optional[str]:
        if result.text is not None:
            try:
                text = self.translation_service.finalize_translation_text(source, result.text)
            except (GeminiContentSafetyException, BtgApiContentSafetyException) as e:
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

    def _record_integrity_one(
        self, input_file_path: Path, temp_dir: Path, idx: int, chunk: List[TranslationUnit], result: BatchItemResult
    ) -> Optional[str]:
        """무결성 응답 하나를 기록한다.

        배치 안에서는 누락 재요청과 분할 재시도를 할 수 없다. 받은 줄은 남기고, 나머지 판단은 실시간
        무결성 이어하기("실시간으로 마무리")에 맡긴다. 그쪽이 같은 설정으로 재요청·분할을 한다.
        """
        ts = self.translation_service
        if result.text is None:
            if result.blocked:
                update_metadata_for_chunk_failure(input_file_path, idx, f"{BLOCKED_PREFIX} {result.error or result.finish_reason}")
                return "blocked"
            update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} {result.error}")
            return "error"

        parsed = ts.parse_integrity_response(chunk, result.text)
        if not parsed.usable:
            # 실시간 경로는 이 경우를 검열의 다른 표현으로 보고 분할한다. 마무리에서 같은 처리를 받는다.
            update_metadata_for_chunk_failure(input_file_path, idx, f"{ERROR_PREFIX} 응답을 줄 단위 JSON으로 해석하지 못했습니다.")
            return "error"

        # 모델이 청크 밖의 ID를 지어내도 다른 줄을 덮지 않게 이 청크의 줄만 남긴다
        chunk_ids = {u.id for u in chunk}
        results = {k: v for k, v in parsed.translated.items() if k in chunk_ids}
        if parsed.missing_ids:
            if int(self.config.get("max_integrity_targeted_retry_depth", 1) or 0) > 0:
                with open(ts.integrity_partial_file(temp_dir, idx), "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False)
                update_metadata_for_chunk_failure(input_file_path, idx, f"{PARTIAL_PREFIX} {len(parsed.missing_ids)}줄")
                return "partial"
            # 누락 재요청을 끈 설정이면 실시간 경로처럼 빠진 줄을 원문으로 두고 완료한다
            for u in chunk:
                if u.id in parsed.missing_ids:
                    results[u.id] = u.text

        with open(ts.integrity_chunk_file(temp_dir, idx), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False)
        ts.integrity_partial_file(temp_dir, idx).unlink(missing_ok=True)
        if self.on_integrity_chunk_translated is not None:
            self.on_integrity_chunk_translated(idx, chunk, results)  # 번역 기억 기록 (AppService)
        source = ts.integrity_chunk_text(chunk)
        translated = "\n".join(str(results.get(u.id, u.text)) for u in chunk)
        update_metadata_for_chunk_completion(input_file_path, idx, len(source), len(translated))
        return None

    async def refresh_async(
        self,
        input_file_path: Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        """진행 중인 작업의 상태를 조회하고, 끝난 작업의 결과를 수거한다."""
        input_file_path = Path(input_file_path)
        metadata = load_metadata(input_file_path) or {}
        batch: Dict[str, Any] = dict(metadata.get("batch") or {})
        pipeline = self._session_pipeline(batch)
        items = self._load_items(input_file_path, pipeline)
        jobs = list(batch.get("jobs") or [])
        active = [j for j in jobs if not j.get("collected")]
        if not active:
            return self.summarize(input_file_path, items)

        # 결과는 청크 번호로 돌아온다. 제출 뒤 경계가 바뀌면 다른 원문 자리에 기록되므로 수거하지 않는다.
        if batch.get("layout") and batch["layout"] != self._layout_signature(pipeline, items):
            raise BtgServiceException(
                "배치를 제출한 뒤 청크 크기·무결성 최대 항목 수·원문 중 하나가 바뀌어 결과를 제자리에 기록할 수 없습니다. "
                "제출 당시 설정으로 되돌린 뒤 다시 확인하세요."
            )

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
                self._record_results(input_file_path, pipeline, items, job, info, batch)
            self._save_batch_section(input_file_path, batch)

        summary = self.summarize(input_file_path, items)
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

    # ------------------------------------------------------------------
    # 최종 파일
    # ------------------------------------------------------------------

    def write_failure_placeholders(self, input_file_path: Path) -> int:
        """[일반] 미완료 청크에 실시간 모드와 같은 실패 표시와 원문을 써서 최종 파일에 빠지지 않게 한다."""
        input_file_path = Path(input_file_path)
        if self.session_pipeline(input_file_path) == PIPELINE_INTEGRITY:
            raise BtgServiceException("무결성 배치는 실패 표시를 넣지 않습니다. assemble_integrity_output을 쓰세요.")
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

    def assemble_integrity_output(self, input_file_path: Path, include_partial: bool = False) -> str:
        """[무결성] 임시 폴더의 결과를 줄 순서로 잇는다. 번역이 없는 줄은 원문이다.

        `include_partial`이면 일부 줄만 받은 청크의 번역도 쓴다("그대로 저장"). 무결성 방식은 줄 위치가
        원문과 맞아야 하므로, 일반 방식처럼 실패 표시를 끼워 넣지 않는다.
        """
        input_file_path = Path(input_file_path)
        ts = self.translation_service
        text = read_text_file(input_file_path)
        temp_dir = self.integrity_temp_dir(input_file_path)
        translated: Dict[str, str] = {}
        for i, chunk in enumerate(ts.split_integrity_chunks(text)):
            result = ts.load_integrity_chunk_result(temp_dir, i, chunk)
            if result is None and include_partial:
                result = ts.load_integrity_partial_result(temp_dir, i, chunk)
            translated.update(result or {})
        return ts.assemble_integrity_text(text.splitlines(), translated)

    @staticmethod
    def metadata_path(input_file_path: Path) -> Path:
        return get_metadata_file_path(input_file_path)
