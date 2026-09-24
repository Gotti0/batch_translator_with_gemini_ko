"""
Translation Memory (번역 장기기억) for Neo Batch Translator (BTG)

원문 문단과 용어집 항목을 임베딩해 입력 파일 옆 `<파일명>_memory/`에 저장하고,
청크를 번역할 때 의미가 가까운 "이미 번역한 문단 쌍"과 용어를 찾아 준다.

- 번역을 시작하기 전에 원문 전체를 한 번 인덱싱한다. 번역 중에는 저장된 벡터만 비교하므로
  임베딩 API를 부르지 않는다.
- 같은 모델·차원이면 내용 해시가 같은 문단은 다시 임베딩하지 않는다 (이어하기·재번역 비용 0).
- 검색은 원문끼리 비교한다. 번역문은 임베딩하지 않고 문단에 붙여 둔다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

MIN_PARAGRAPH_CHARS = 20  # 이보다 짧은 문단은 앞 문단에 붙여 한 단위로 본다
MAX_EXAMPLE_CHARS = 600  # 주입하는 예시 한쪽(원문·번역문)의 최대 글자 수


def split_paragraphs(text: str, min_chars: int = MIN_PARAGRAPH_CHARS) -> List[str]:
    """청크를 기억 단위(문단)로 나눈다. 인덱싱과 번역문 기록이 같은 규칙을 써야 1:1로 짝지어진다.

    빈 줄이 있으면 빈 줄로, 없으면 줄바꿈으로 나눈다. 짧은 문단은 앞 문단에 붙인다.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    merged: List[str] = []
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if merged and (len(merged[-1]) < min_chars or len(part) < min_chars):
            merged[-1] = f"{merged[-1]}\n{part}"
        else:
            merged.append(part)
    return merged


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _clip(text: str, limit: int = MAX_EXAMPLE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass
class MemoryExample:
    source: str
    translation: str
    chunk_index: int
    score: float


class TranslationMemoryStore:
    """입력 파일 하나의 번역 기억."""

    def __init__(self, directory: Path, model: str, dimension: Optional[int]) -> None:
        self.directory = Path(directory)
        self.model = model
        self.dimension = dimension
        self.items: List[Dict[str, Any]] = []
        self.vectors = np.zeros((0, dimension or 0), dtype=np.float32)
        self._hash_rows: Dict[str, List[int]] = {}
        self.stats = {"recorded_chunks": 0, "mismatched_chunks": 0}

    # ------------------------------------------------------------------
    # 경로·저장
    # ------------------------------------------------------------------

    @staticmethod
    def directory_for(input_file_path: Path) -> Path:
        p = Path(input_file_path)
        return p.parent / f"{p.stem}_memory"

    @classmethod
    def open(cls, input_file_path: Path, model: str, dimension: Optional[int]) -> "TranslationMemoryStore":
        """저장된 기억을 연다. 모델·차원이 다르거나 파일이 깨졌으면 빈 저장소로 시작한다."""
        store = cls(cls.directory_for(input_file_path), model, dimension)
        meta_path = store.directory / "meta.json"
        if not meta_path.exists():
            return store
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("model") != model or meta.get("dimension") != dimension:
                logger.info(f"번역 기억: 모델/차원이 바뀌어 새로 만듭니다 ({meta.get('model')}/{meta.get('dimension')} → {model}/{dimension})")
                return store
            vectors = np.load(store.directory / "vectors.npy")
            items = [json.loads(line) for line in (store.directory / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(items) != len(vectors):
                raise ValueError(f"항목 {len(items)}개와 벡터 {len(vectors)}개가 다릅니다")
            store.items, store.vectors = items, vectors.astype(np.float32)
            store._rebuild_index()
            logger.info(f"번역 기억 로드: {len(items)}개 항목 ({store.directory})")
        except Exception as e:
            logger.warning(f"번역 기억을 읽지 못해 새로 만듭니다: {e}")
            store = cls(store.directory, model, dimension)
        return store

    def save(self, vectors_changed: bool = True) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if vectors_changed:
            np.save(self.directory / "vectors.npy", self.vectors)
        with open(self.directory / "items.jsonl", "w", encoding="utf-8") as f:
            for item in self.items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        meta = {"model": self.model, "dimension": self.dimension, "updated_at": time.time(), "count": len(self.items)}
        (self.directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _rebuild_index(self) -> None:
        self._hash_rows = {}
        for row, item in enumerate(self.items):
            self._hash_rows.setdefault(item["hash"], []).append(row)

    # ------------------------------------------------------------------
    # 인덱싱
    # ------------------------------------------------------------------

    async def _index(self, kind: str, entries: List[Dict[str, Any]], embedder: Any) -> int:
        """`kind` 항목을 `entries`로 교체한다. 같은 해시의 기존 벡터는 재사용하고 새 텍스트만 임베딩한다."""
        cache: Dict[str, np.ndarray] = {}
        for row, item in enumerate(self.items):
            if item["kind"] == kind:
                cache.setdefault(item["hash"], self.vectors[row])
        missing = sorted({e["hash"]: e["text"] for e in entries if e["hash"] not in cache}.items())
        if missing:
            new_vectors = await embedder.embed_documents([text for _, text in missing])
            for (h, _), vec in zip(missing, new_vectors):
                cache[h] = _normalize(np.asarray(vec, dtype=np.float32))

        keep_rows = [row for row, item in enumerate(self.items) if item["kind"] != kind]
        items = [self.items[row] for row in keep_rows] + entries
        parts = [self.vectors[keep_rows]] if keep_rows else []
        if entries:
            parts.append(np.stack([cache[e["hash"]] for e in entries]))
        dim = self.dimension or (parts[0].shape[1] if parts and len(parts[0]) else 0)
        self.vectors = np.concatenate(parts).astype(np.float32) if parts else np.zeros((0, dim), dtype=np.float32)
        self.items = items
        self._rebuild_index()
        return len(missing)

    async def index_source_async(self, chunks: Sequence[str], embedder: Any) -> int:
        """원문 청크의 문단을 인덱싱한다. 이미 기록된 번역문은 (청크 번호, 해시)가 같으면 유지한다."""
        previous = {(i.get("chunk_index"), i["hash"], i.get("para_index")): i.get("translation")
                    for i in self.items if i["kind"] == "paragraph"}
        entries = []
        for chunk_index, chunk in enumerate(chunks):
            for para_index, para in enumerate(split_paragraphs(chunk)):
                h = content_hash(para)
                entries.append({
                    "kind": "paragraph", "text": para, "hash": h, "chunk_index": chunk_index,
                    "para_index": para_index, "translation": previous.get((chunk_index, h, para_index)),
                })
        embedded = await self._index("paragraph", entries, embedder)
        self.save()
        logger.info(f"번역 기억 인덱싱: 문단 {len(entries)}개 (새로 임베딩 {embedded}개)")
        return embedded

    async def index_glossary_async(self, entries: Iterable[Any], embedder: Any) -> int:
        """용어집 항목을 `키워드 (번역어)` 텍스트로 인덱싱한다."""
        rows = []
        for e in entries:
            text = f"{e.keyword} ({e.translated_keyword})"
            rows.append({"kind": "glossary", "text": text, "hash": content_hash(text), "keyword": e.keyword})
        embedded = await self._index("glossary", rows, embedder)
        self.save()
        logger.info(f"번역 기억 인덱싱: 용어 {len(rows)}개 (새로 임베딩 {embedded}개)")
        return embedded

    # ------------------------------------------------------------------
    # 번역문 기록
    # ------------------------------------------------------------------

    def record_translation(self, chunk_index: int, source_chunk: str, translated_chunk: str, save: bool = True) -> bool:
        """번역된 청크를 문단 단위로 짝지어 기록한다. 문단 수가 다르면 기록하지 않는다."""
        sources = split_paragraphs(source_chunk)
        translations = split_paragraphs(translated_chunk)
        self.stats["recorded_chunks"] += 1
        if not sources or len(sources) != len(translations):
            self.stats["mismatched_chunks"] += 1
            logger.debug(f"번역 기억: 청크 {chunk_index} 문단 수 불일치 (원문 {len(sources)}, 번역 {len(translations)})")
            return False
        changed = False
        for para_index, (src, tr) in enumerate(zip(sources, translations)):
            for row in self._hash_rows.get(content_hash(src), []):
                item = self.items[row]
                if item["kind"] == "paragraph" and item.get("chunk_index") == chunk_index and item.get("para_index") == para_index:
                    item["translation"] = tr
                    changed = True
        if changed and save:
            self.save(vectors_changed=False)
        return changed

    # ------------------------------------------------------------------
    # 검색
    # ------------------------------------------------------------------

    def _query_rows(self, text: str) -> List[int]:
        rows = []
        for para in split_paragraphs(text):
            rows.extend(r for r in self._hash_rows.get(content_hash(para), []) if self.items[r]["kind"] == "paragraph")
        return sorted(set(rows))

    def _scores(self, query_rows: List[int], candidate_rows: List[int]) -> np.ndarray:
        """후보마다 쿼리 문단들과의 코사인 유사도 최댓값 (벡터는 정규화되어 있다)."""
        if not query_rows or not candidate_rows:
            return np.zeros(0, dtype=np.float32)
        sims = self.vectors[candidate_rows] @ self.vectors[query_rows].T
        return sims.max(axis=1)

    def search_examples(self, text: str, top_k: int = 3, min_similarity: float = 0.55) -> List[MemoryExample]:
        """`text`(번역할 청크)와 의미가 가까운, 이미 번역된 문단 쌍을 찾는다.

        쿼리 벡터는 저장된 원문 문단 벡터를 재사용한다. 인덱싱되지 않은 텍스트(분할 재시도의 조각 등)는
        결과가 없다.
        """
        query_rows = self._query_rows(text)
        if not query_rows or top_k <= 0:
            return []
        query_hashes = {self.items[r]["hash"] for r in query_rows}
        candidates = [
            r for r, item in enumerate(self.items)
            if item["kind"] == "paragraph" and item.get("translation") and item["hash"] not in query_hashes
        ]
        scores = self._scores(query_rows, candidates)
        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        results, seen = [], set()
        for row, score in ranked:
            if score < min_similarity or len(results) >= top_k:
                break
            item = self.items[row]
            if item["hash"] in seen:
                continue
            seen.add(item["hash"])
            results.append(MemoryExample(item["text"], item["translation"], int(item.get("chunk_index", -1)), float(score)))
        return results

    def search_glossary(self, text: str, top_k: int = 5, min_similarity: float = 0.6) -> List[str]:
        """`text`와 의미가 가까운 용어집 키워드를 찾는다."""
        query_rows = self._query_rows(text)
        candidates = [r for r, item in enumerate(self.items) if item["kind"] == "glossary"]
        scores = self._scores(query_rows, candidates)
        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        return [self.items[r]["keyword"] for r, s in ranked[:top_k] if s >= min_similarity]

    @staticmethod
    def format_examples(examples: List[MemoryExample]) -> str:
        if not examples:
            return ""
        lines = [
            "<translation_memory>",
            "이전에 번역한 비슷한 문단입니다. 참고용이며, 호칭·말투·용어를 이와 일관되게 유지하세요.",
        ]
        for ex in examples:
            lines.append(f'<example chunk="{ex.chunk_index + 1}">')
            lines.append(f"<source>{_clip(ex.source)}</source>")
            lines.append(f"<translation>{_clip(ex.translation)}</translation>")
            lines.append("</example>")
        lines.append("</translation_memory>")
        return "\n".join(lines)

    def summary(self) -> Dict[str, int]:
        paragraphs = [i for i in self.items if i["kind"] == "paragraph"]
        return {
            "paragraphs": len(paragraphs),
            "translated": sum(1 for i in paragraphs if i.get("translation")),
            "glossary": sum(1 for i in self.items if i["kind"] == "glossary"),
        }


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
