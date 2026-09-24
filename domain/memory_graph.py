"""
Memory Graph (WygLore Leaf 방식 연상 기억) for Neo Batch Translator (BTG)

번역 기억을 "살아 자라는 뜰"로 다룬다. 번역 작업에 맞춰 옮긴 WygLore Leaf의 원칙:

- 캐논과 성장의 분리: 용어집 항목(canon)은 사용자가 정한 고정 정보다. 감쇠하지도, 가라앉지도,
  자동 추출로 덮이지도 않는다. 번역하며 자라는 기억(entity·episode)은 성장 영역이다.
- 점화(trigger): 청크 원문에 이름·별칭이 직접 나온 가지는 1.0으로 점화된다 (직접 호명).
  의미가 가까운 번역 문단(episode)은 임베딩 유사도만큼 점화된다.
- 확산(propagate): 점화된 가지에서 연결된 가지로 가중치 × 감쇠율만큼 퍼진다 (max_hops).
- 헵 강화(reinforce): 한 청크에서 함께 쓰인 가지끼리 연결이 강해진다.
- 호박(amber): 오래 쓰이지 않은 성장 가지는 가라앉아 확산으로 닿지 않는다. 삭제하지 않으며,
  직접 호명되거나 강한 확산이 닿으면 다시 깨어난다.
- 시간을 서술어로: 청크 거리를 "바로 앞", "12청크 전 · 가라앉는 중", "오래 전 · 호박 속"으로 바꿔 붙인다.

시간 축은 벽시계가 아니라 청크 번호다. 활성도 감쇠는 저장된 값에 청크 거리를 적용해 계산하므로,
병렬·배치 번역에서 청크가 순서 없이 처리되어도 결과가 같다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

# 깊이 프리셋 (WygLore Leaf의 빠르게 / 무난하게 / 깊게)
DEPTH_PRESETS: Dict[str, Dict[str, float]] = {
    "fast": {"max_hops": 0, "wake_threshold": 1.01},      # 표면 결만 본다
    "balanced": {"max_hops": 1, "wake_threshold": 0.6},   # 강한 결합은 멀리도 미친다
    "deep": {"max_hops": 2, "wake_threshold": 0.3},       # 호박 속까지 들춰본다
}

EDGE_INIT = 0.3          # 에피소드↔용어 첫 연결 가중치
REINFORCE_DELTA = 0.1    # 함께 점화될 때 늘어나는 가중치
PROPAGATION_DECAY = 0.5  # 한 홉 건널 때마다 곱하는 값
MAX_FANOUT = 20          # 한 가지에서 확산할 이웃 수 상한
MAX_EVIDENCE = 3
MAX_MERGE_LOG = 10


@dataclass
class MemoryUnit:
    id: str
    kind: str                 # canon | entity | episode
    name: str
    aliases: List[str] = field(default_factory=list)
    translated: str = ""
    note: str = ""
    category: str = ""        # entity: character / place / item / other
    born_chunk: int = -1
    last_fired_chunk: int = -1
    fire_count: int = 0
    evidence: List[Dict[str, Any]] = field(default_factory=list)  # 원문 닻: [{"chunk": i, "excerpt": "..."}]
    hash: str = ""            # episode: 기억 저장소의 문단 해시

    def surface_names(self) -> List[str]:
        return [n for n in [self.name, *self.aliases] if n and len(n) >= 2]


@dataclass
class RecalledUnit:
    unit: MemoryUnit
    activation: float
    direct: bool              # 원문에 직접 호명되었는가 (두 결의 합류에서 위로)
    time_label: str


@dataclass
class MemoryRecall:
    chunk_index: Optional[int]
    episodes: List[RecalledUnit]
    entities: List[RecalledUnit]
    canon_keywords: List[str]

    @property
    def fired_ids(self) -> List[str]:
        return [r.unit.id for r in self.episodes + self.entities] + [f"canon:{k}" for k in self.canon_keywords]


def time_label(distance: Optional[int], cold: bool) -> str:
    """청크 거리를 LLM이 읽을 수 있는 시간 서술어로 바꾼다."""
    if cold:
        return "오래 전 · 호박 속"
    if distance is None or distance < 0:
        return ""
    if distance == 0:
        return "같은 장면"
    if distance <= 2:
        return "바로 앞"
    return f"{distance}청크 전"


def _edge_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


class MemoryGraph:
    """캐논·인물·에피소드 가지와 그 연결."""

    def __init__(self, cold_after_chunks: int = 30) -> None:
        self.units: Dict[str, MemoryUnit] = {}
        self.edges: Dict[str, float] = {}
        self.neighbors: Dict[str, Dict[str, float]] = {}
        self.merge_log: List[Dict[str, Any]] = []
        self.cold_after_chunks = cold_after_chunks
        self._pending_recalls: Dict[int, List[str]] = {}

    # ------------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        data = {
            "version": 1,
            "saved_at": time.time(),
            "units": [asdict(u) for u in self.units.values()],
            "edges": self.edges,
            "merge_log": self.merge_log[-MAX_MERGE_LOG:],
        }
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, cold_after_chunks: int = 30) -> "MemoryGraph":
        graph = cls(cold_after_chunks)
        path = Path(path)
        if not path.exists():
            return graph
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for u in data.get("units", []):
                unit = MemoryUnit(**u)
                graph.units[unit.id] = unit
            for key, w in (data.get("edges") or {}).items():
                a, b = key.split("|", 1)
                graph._set_edge(a, b, float(w))
            graph.merge_log = list(data.get("merge_log") or [])
        except Exception as e:
            logger.warning(f"기억 그래프를 읽지 못해 새로 만듭니다: {e}")
            return cls(cold_after_chunks)
        return graph

    # ------------------------------------------------------------------
    # 가지·연결
    # ------------------------------------------------------------------

    def _set_edge(self, a: str, b: str, w: float) -> None:
        if a == b:
            return
        w = max(0.0, min(1.0, w))
        self.edges[_edge_key(a, b)] = w
        self.neighbors.setdefault(a, {})[b] = w
        self.neighbors.setdefault(b, {})[a] = w

    def _bump_edge(self, a: str, b: str, delta: float, init: float = 0.0) -> None:
        current = self.edges.get(_edge_key(a, b))
        self._set_edge(a, b, (init if current is None else current) + delta)

    def is_cold(self, unit: MemoryUnit, chunk_index: Optional[int]) -> bool:
        """호박 속인가: 캐논은 가라앉지 않는다. 성장 가지는 오래 점화되지 않으면 가라앉는다."""
        if unit.kind == "canon" or chunk_index is None:
            return False
        last = unit.last_fired_chunk if unit.last_fired_chunk >= 0 else unit.born_chunk
        return last >= 0 and chunk_index - last > self.cold_after_chunks

    def sync_canon(self, entries: Iterable[Any]) -> None:
        """용어집(캐논)을 가지로 맞춘다. 캐논 내용은 항상 용어집이 기준이다."""
        seen: Set[str] = set()
        for e in entries:
            uid = f"canon:{e.keyword}"
            seen.add(uid)
            unit = self.units.get(uid)
            if unit is None:
                self.units[uid] = MemoryUnit(id=uid, kind="canon", name=e.keyword, translated=e.translated_keyword)
            else:
                unit.translated = e.translated_keyword
        for uid in [u for u, unit in self.units.items() if unit.kind == "canon" and u not in seen]:
            self._remove_unit(uid)

    def _remove_unit(self, uid: str) -> None:
        self.units.pop(uid, None)
        for other in list(self.neighbors.pop(uid, {})):
            self.neighbors.get(other, {}).pop(uid, None)
            self.edges.pop(_edge_key(uid, other), None)

    def _find_entity(self, names: Iterable[str]) -> Optional[MemoryUnit]:
        wanted = {n.strip().lower() for n in names if n and n.strip()}
        for unit in self.units.values():
            if unit.kind == "entity" and wanted & {n.lower() for n in unit.surface_names()}:
                return unit
        return None

    def upsert_entity(
        self,
        name: str,
        aliases: Iterable[str] = (),
        translated: str = "",
        note: str = "",
        category: str = "",
        chunk_index: int = -1,
        excerpt: str = "",
    ) -> MemoryUnit:
        """추출된 인물·고유명사를 성장 가지로 넣는다. 이름·별칭이 겹치면 기존 가지에 흡수한다."""
        name = name.strip()
        aliases = [a.strip() for a in aliases if a and a.strip() and a.strip() != name]
        existing = self._find_entity([name, *aliases])
        if existing is None:
            unit = MemoryUnit(
                id=f"ent:{name}", kind="entity", name=name, aliases=aliases, translated=translated,
                note=note, category=category, born_chunk=chunk_index, last_fired_chunk=chunk_index,
            )
            self.units[unit.id] = unit
        else:
            unit = existing
            if name.lower() != unit.name.lower():
                self.merge_log.append({"into": unit.name, "absorbed": name, "chunk": chunk_index, "at": time.time()})
                self.merge_log = self.merge_log[-MAX_MERGE_LOG:]
            for n in [name, *aliases]:
                if n.lower() not in {x.lower() for x in unit.surface_names()} and n != unit.name:
                    unit.aliases.append(n)
            if translated:
                unit.translated = translated
            if note:
                unit.note = note  # 최근 관찰이 우선 (말투·호칭은 이야기 중 바뀔 수 있다)
            if category:
                unit.category = category
        if excerpt:
            unit.evidence = (unit.evidence + [{"chunk": chunk_index, "excerpt": excerpt[:120]}])[-MAX_EVIDENCE:]
        return unit

    # ------------------------------------------------------------------
    # 점화·확산 (recall은 상태를 바꾸지 않는다)
    # ------------------------------------------------------------------

    def surface_triggers(self, text: str) -> List[str]:
        """원문에 이름·별칭이 직접 나온 캐논·인물 가지."""
        lowered = text.lower()
        hits = []
        for unit in self.units.values():
            if unit.kind in ("canon", "entity") and any(n.lower() in lowered for n in unit.surface_names()):
                hits.append(unit.id)
        return hits

    def recall(
        self,
        text: str,
        chunk_index: Optional[int],
        episode_seeds: List[Tuple[str, float]],
        depth: str = "balanced",
        max_episodes: int = 3,
        max_entities: int = 5,
        min_activation: float = 0.25,
    ) -> MemoryRecall:
        """이 청크에서 떠오르는 기억을 계산한다.

        episode_seeds: 임베딩 유사도로 찾은 (문단 해시, 유사도) — 의미가 가까운 번역 문단.
        """
        preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS["balanced"])
        activation: Dict[str, float] = {}
        direct: Set[str] = set()

        for uid in self.surface_triggers(text):
            activation[uid] = 1.0
            direct.add(uid)
        for h, score in episode_seeds:
            uid = f"ep:{h}"
            if uid in self.units:
                activation[uid] = max(activation.get(uid, 0.0), float(score))

        frontier = dict(activation)
        for _ in range(int(preset["max_hops"])):
            nxt: Dict[str, float] = {}
            for uid, act in frontier.items():
                ranked = sorted(self.neighbors.get(uid, {}).items(), key=lambda x: -x[1])[:MAX_FANOUT]
                for nb, w in ranked:
                    unit = self.units.get(nb)
                    if unit is None:
                        continue
                    spread = act * w * PROPAGATION_DECAY
                    # 호박 속 가지는 강한 확산이 닿을 때만 깨어난다
                    if self.is_cold(unit, chunk_index) and spread < preset["wake_threshold"] * PROPAGATION_DECAY:
                        continue
                    if spread > nxt.get(nb, 0.0):
                        nxt[nb] = spread
            for uid, act in nxt.items():
                if act > activation.get(uid, 0.0):
                    activation[uid] = act
            frontier = nxt
            if not frontier:
                break

        def recalled(kind: str, limit: int) -> List[RecalledUnit]:
            rows = []
            for uid, act in activation.items():
                unit = self.units.get(uid)
                if unit is None or unit.kind != kind or act < min_activation:
                    continue
                if kind == "episode" and chunk_index is not None and unit.born_chunk == chunk_index:
                    continue  # 자기 자신의 문단은 예시가 아니다
                ref = unit.born_chunk if kind == "episode" else unit.last_fired_chunk
                distance = (chunk_index - ref) if (chunk_index is not None and ref >= 0) else None
                rows.append(RecalledUnit(unit, act, uid in direct, time_label(distance, self.is_cold(unit, chunk_index))))
            # 두 결의 합류: 직접 호명된 가지가 먼저, 그다음 점화 강도
            rows.sort(key=lambda r: (not r.direct, -r.activation))
            return rows[:limit]

        episodes = recalled("episode", max_episodes)
        entities = recalled("entity", max_entities)
        canon = [self.units[u].name for u in activation
                 if u in self.units and self.units[u].kind == "canon" and activation[u] >= min_activation]
        result = MemoryRecall(chunk_index, episodes, entities, canon)
        if chunk_index is not None:
            self._pending_recalls[chunk_index] = result.fired_ids + [u for u in direct]
        return result

    # ------------------------------------------------------------------
    # 청크 종료 (상태 갱신)
    # ------------------------------------------------------------------

    def add_episode(self, para_hash: str, text: str, chunk_index: int) -> MemoryUnit:
        uid = f"ep:{para_hash}"
        unit = self.units.get(uid)
        if unit is None:
            unit = MemoryUnit(id=uid, kind="episode", name=text[:30], born_chunk=chunk_index,
                              last_fired_chunk=chunk_index, hash=para_hash)
            self.units[uid] = unit
        # 이 문단에 등장한 캐논·인물과 연결 (원문 닻)
        lowered = text.lower()
        for other in list(self.units.values()):
            if other.kind in ("canon", "entity") and any(n.lower() in lowered for n in other.surface_names()):
                if _edge_key(uid, other.id) not in self.edges:
                    self._set_edge(uid, other.id, EDGE_INIT)
        return unit

    def close_chunk(self, chunk_index: int, extra_fired: Iterable[str] = ()) -> None:
        """청크가 끝나면 함께 쓰인 가지를 강화하고 점화 기록을 남긴다 (헵 강화)."""
        fired = [u for u in dict.fromkeys([*self._pending_recalls.pop(chunk_index, []), *extra_fired]) if u in self.units]
        for i, a in enumerate(fired):
            unit = self.units[a]
            unit.fire_count += 1
            unit.last_fired_chunk = max(unit.last_fired_chunk, chunk_index)
            for b in fired[i + 1:]:
                self._bump_edge(a, b, REINFORCE_DELTA, init=0.0)

    def link_cooccurring(self, uids: List[str], chunk_index: int) -> None:
        """같은 청크에 등장한 캐논·인물끼리 연결한다."""
        uids = [u for u in dict.fromkeys(uids) if u in self.units]
        for i, a in enumerate(uids):
            for b in uids[i + 1:]:
                self._bump_edge(a, b, REINFORCE_DELTA, init=EDGE_INIT - REINFORCE_DELTA)

    def summary(self, chunk_index: Optional[int] = None) -> Dict[str, int]:
        kinds = {"canon": 0, "entity": 0, "episode": 0}
        cold = 0
        for u in self.units.values():
            kinds[u.kind] = kinds.get(u.kind, 0) + 1
            if chunk_index is not None and self.is_cold(u, chunk_index):
                cold += 1
        return {**kinds, "edges": len(self.edges), "cold": cold}


def format_recall(recall: MemoryRecall, example_lookup) -> str:
    """떠오른 기억을 프롬프트 블록으로 만든다. 캐논(용어집)은 용어집 컨텍스트로 따로 들어간다."""
    lines: List[str] = []
    if recall.entities:
        lines.append("<characters>")
        for r in recall.entities:
            u = r.unit
            attrs = f'name="{u.name}"'
            if u.translated:
                attrs += f' ko="{u.translated}"'
            if r.time_label:
                attrs += f' last_seen="{r.time_label}"'
            lines.append(f"<entity {attrs}>{u.note}</entity>")
        lines.append("</characters>")
    examples = []
    for r in recall.episodes:
        ex = example_lookup(r.unit.hash)
        if ex is None:
            continue
        source, translation, chunk = ex
        label = f' time="{r.time_label}"' if r.time_label else ""
        examples.append(f'<example chunk="{chunk + 1}"{label}>\n<source>{source}</source>\n<translation>{translation}</translation>\n</example>')
    if examples:
        lines.append("<examples>")
        lines.extend(examples)
        lines.append("</examples>")
    if not lines:
        return ""
    head = [
        "<translation_memory>",
        "앞서 번역하며 쌓인 기억입니다. 참고용이며, 인물의 호칭·말투와 용어를 이와 일관되게 유지하세요. "
        "용어집과 다르면 용어집을 따르세요.",
    ]
    return "\n".join(head + lines + ["</translation_memory>"])
