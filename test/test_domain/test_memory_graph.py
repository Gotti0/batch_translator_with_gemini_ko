"""
MemoryGraph (WygLore Leaf 방식 연상 기억) 테스트.
"""

import pytest

from core.dtos import GlossaryEntryDTO
from domain.memory_extractor import ExtractedEntity, MemoryExtractor, _to_dicts, find_excerpt
from domain.memory_graph import MemoryGraph, format_recall, time_label


def _canon(*pairs):
    return [GlossaryEntryDTO(keyword=k, translated_keyword=v, target_language="ko", occurrence_count=1) for k, v in pairs]


def test_time_labels():
    assert time_label(0, False) == "같은 장면"
    assert time_label(2, False) == "바로 앞"
    assert time_label(12, False) == "12청크 전"
    assert time_label(50, True) == "오래 전 · 호박 속"
    assert time_label(None, False) == ""


def test_surface_trigger_is_direct_and_ranks_first():
    g = MemoryGraph()
    g.sync_canon(_canon(("魔王", "마왕")))
    g.upsert_entity("リリア", aliases=["リリちゃん"], translated="릴리아", note="반말, 주인공을 '선배'라 부름", chunk_index=0)
    g.upsert_entity("ガルド", translated="가르드", note="존댓말", chunk_index=0)
    r = g.recall("リリちゃんは魔王を見た。", chunk_index=1, episode_seeds=[])
    assert [e.unit.name for e in r.entities] == ["リリア"]  # 별칭으로 호명
    assert r.entities[0].direct and r.entities[0].activation == 1.0
    assert r.canon_keywords == ["魔王"]


def test_propagation_reinforcement_and_depth():
    g = MemoryGraph()
    g.upsert_entity("リリア", translated="릴리아", note="반말", chunk_index=0)
    g.upsert_entity("ガルド", translated="가르드", note="존댓말", chunk_index=0)
    # 두 인물이 여러 청크에 함께 등장 → 연결 강화
    for i in range(3):
        g.link_cooccurring(["ent:リリア", "ent:ガルド"], i)
    w = g.edges["ent:ガルド|ent:リリア"]
    assert w == pytest.approx(0.5)

    # 'リリア'만 호명돼도 강하게 연결된 'ガルド'가 확산으로 떠오른다 (무난하게: 1홉)
    r = g.recall("リリアが笑った。", chunk_index=3, episode_seeds=[], depth="balanced", min_activation=0.2)
    names = {e.unit.name: e for e in r.entities}
    assert "ガルド" in names and not names["ガルド"].direct
    assert r.entities[0].unit.name == "リリア"  # 직접 호명이 위로

    # 빠르게: 확산 없음
    r = g.recall("リリアが笑った。", chunk_index=3, episode_seeds=[], depth="fast", min_activation=0.2)
    assert [e.unit.name for e in r.entities] == ["リリア"]


def test_close_chunk_hebbian_and_last_fired():
    g = MemoryGraph()
    g.upsert_entity("A子", chunk_index=0)
    g.upsert_entity("B男", chunk_index=0)
    g.recall("A子とB男", chunk_index=5, episode_seeds=[])
    g.close_chunk(5)
    assert g.units["ent:A子"].last_fired_chunk == 5 and g.units["ent:A子"].fire_count == 1
    assert g.edges["ent:A子|ent:B男"] == pytest.approx(0.1)


def test_amber_cold_units_wake_only_on_strong_signal():
    g = MemoryGraph(cold_after_chunks=10)
    g.sync_canon(_canon(("聖剣", "성검")))
    g.upsert_entity("老師", translated="노사", note="존댓말", chunk_index=0)
    g._set_edge("canon:聖剣", "ent:老師", 0.5)

    # 캐논은 가라앉지 않는다, 성장 가지는 가라앉는다
    assert not g.is_cold(g.units["canon:聖剣"], 100)
    assert g.is_cold(g.units["ent:老師"], 50)

    # 약한 확산(1.0 × 0.5 × 0.5 = 0.25)은 호박을 깨지 못한다 (무난하게: 깨우는 기준 0.6)
    r = g.recall("聖剣が光った。", chunk_index=50, episode_seeds=[], depth="balanced", min_activation=0.1)
    assert r.entities == []
    # 깊게: 호박 속까지 들춰본다
    r = g.recall("聖剣が光った。", chunk_index=50, episode_seeds=[], depth="deep", min_activation=0.1)
    assert [e.unit.name for e in r.entities] == ["老師"]
    assert r.entities[0].time_label == "오래 전 · 호박 속"
    # 직접 호명되면 언제든 떠오른다
    r = g.recall("老師が言った。", chunk_index=50, episode_seeds=[], depth="fast")
    assert [e.unit.name for e in r.entities] == ["老師"]


def test_episode_seed_propagates_to_entity_and_excludes_own_chunk():
    g = MemoryGraph()
    g.upsert_entity("リリア", translated="릴리아", note="반말", chunk_index=0)
    g.add_episode("h1", "リリアは剣を抜いた。", chunk_index=0)
    g.add_episode("h2", "リリアは空を見た。", chunk_index=4)
    assert "ent:リリア|ep:h1" in g.edges
    r = g.recall("剣を構えた。", chunk_index=4, episode_seeds=[("h1", 0.9), ("h2", 0.95)], depth="deep", min_activation=0.1)
    assert [e.unit.hash for e in r.episodes] == ["h1"]  # 같은 청크(4)의 문단은 예시가 아니다
    assert r.episodes[0].time_label == "4청크 전"


def test_upsert_merges_aliases_and_logs():
    g = MemoryGraph()
    g.upsert_entity("リリア", translated="릴리아", note="반말", chunk_index=0, excerpt="「行くよ」とリリアが言った。")
    g.upsert_entity("リリちゃん", aliases=["リリア"], note="주인공에게만 존댓말", chunk_index=3, excerpt="リリちゃんが笑った")
    ents = [u for u in g.units.values() if u.kind == "entity"]
    assert len(ents) == 1
    u = ents[0]
    assert "リリちゃん" in u.aliases and u.note == "주인공에게만 존댓말"
    assert [e["chunk"] for e in u.evidence] == [0, 3]
    assert g.merge_log[-1]["absorbed"] == "リリちゃん" and g.merge_log[-1]["into"] == "リリア"


def test_canon_sync_removes_deleted_terms_and_save_load(tmp_path):
    g = MemoryGraph()
    g.sync_canon(_canon(("魔王", "마왕"), ("勇者", "용사")))
    g.upsert_entity("リリア", chunk_index=0)
    g._set_edge("canon:勇者", "ent:リリア", 0.4)
    g.sync_canon(_canon(("魔王", "마왕님")))
    assert "canon:勇者" not in g.units and "canon:勇者|ent:リリア" not in g.edges
    assert g.units["canon:魔王"].translated == "마왕님"

    path = tmp_path / "graph.json"
    g.save(path)
    g2 = MemoryGraph.load(path)
    assert set(g2.units) == set(g.units)
    assert g2.summary() == g.summary()


def test_format_recall():
    g = MemoryGraph()
    g.upsert_entity("リリア", translated="릴리아", note="반말, '선배'라 부름", chunk_index=0)
    g.add_episode("h1", "リリアは剣を抜いた。", chunk_index=0)
    r = g.recall("リリアが来た。", chunk_index=3, episode_seeds=[("h1", 0.8)])
    block = format_recall(r, lambda h: ("リリアは剣を抜いた。", "릴리아는 검을 뽑았다.", 0))
    assert block.startswith("<translation_memory>")
    assert '<entity name="リリア" ko="릴리아" last_seen="3청크 전">반말, \'선배\'라 부름</entity>' in block
    assert '<example chunk="1" time="3청크 전">' in block
    assert "용어집을 따르세요" in block
    assert format_recall(g.recall("無関係", chunk_index=3, episode_seeds=[]), lambda h: None) == ""


def test_extractor_response_normalization():
    assert _to_dicts('```json\n[{"name": "リリア", "note": "반말"}]\n```')[0]["name"] == "リリア"
    assert _to_dicts({"entities": [{"name": "A"}]}) == [{"name": "A"}]
    assert _to_dicts([ExtractedEntity(name="B")])[0]["name"] == "B"
    assert _to_dicts("not json") == [] and _to_dicts(None) == []
    assert _to_dicts([{"name": ""}, "x"]) == []
    assert find_excerpt("一行目\n「行くよ」とリリアが言った。\n三行目", ["リリア"]) == "「行くよ」とリリアが言った。"


@pytest.mark.asyncio
async def test_extractor_calls_llm_with_schema():
    from unittest.mock import AsyncMock, MagicMock
    client = MagicMock()
    client.generate_text_async = AsyncMock(return_value=[{"name": "リリア", "aliases": ["リリ"], "translated_name": "릴리아",
                                                          "category": "character", "note": "반말", "extra": 1}])
    entities = await MemoryExtractor(client, "gemini-lite").extract("原文", "번역", ["ガルド"])
    assert entities[0].translated_name == "릴리아" and entities[0].aliases == ["リリ"]
    kwargs = client.generate_text_async.call_args.kwargs
    assert kwargs["model_name"] == "gemini-lite"
    assert kwargs["generation_config_dict"]["response_mime_type"] == "application/json"
    assert "ガルド" in kwargs["prompt"]
