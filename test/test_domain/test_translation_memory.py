"""
TranslationMemoryStore 테스트 (가짜 임베딩: 글자 bigram 해시 벡터).
"""

import zlib

import numpy as np
import pytest

from core.dtos import GlossaryEntryDTO
from domain.translation_memory import TranslationMemoryStore, split_paragraphs


class FakeEmbedder:
    """글자 bigram을 해시해 만든 벡터. 글자를 많이 공유하는 문장끼리 유사도가 높다."""

    def __init__(self, model="fake-1", dim=64):
        self.model, self.output_dimension, self.dim = model, dim, dim
        self.calls = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [self.vec(t) for t in texts]

    def vec(self, text):
        v = np.zeros(self.dim, dtype=np.float32)
        for a, b in zip(text, text[1:]):
            v[zlib.crc32((a + b).encode()) % self.dim] += 1.0
        return v.tolist()


CHUNKS = [
    "勇者リリアは剣を抜いた。「待っていろ、魔王」\n\n夜の森は静かだった。月が雲に隠れている。",
    "勇者リリアは剣を構えた。「逃がさないぞ、魔王」\n\n朝の市場はにぎやかだった。パンの匂いがする。",
    "魔王城の門が開いた。黒い霧が流れ出してくる。\n\n勇者リリアは剣を握りしめた。「ここで終わらせる」",
]


def test_split_paragraphs():
    assert split_paragraphs("긴 첫 문단입니다. 스무 자를 넘도록 채웁니다.\n\n두 번째 문단도 스무 자를 넘도록 채웁니다.") == [
        "긴 첫 문단입니다. 스무 자를 넘도록 채웁니다.", "두 번째 문단도 스무 자를 넘도록 채웁니다."]
    # 빈 줄이 없으면 줄바꿈 기준, 짧은 줄은 앞과 합친다
    assert split_paragraphs("가나다\n라마바사아자차카타파하 가나다라마바사아\n") == ["가나다\n라마바사아자차카타파하 가나다라마바사아"]
    assert split_paragraphs("   \n\n ") == []


@pytest.mark.asyncio
async def test_index_cache_and_reopen(tmp_path):
    inp = tmp_path / "novel.txt"
    emb = FakeEmbedder()
    store = TranslationMemoryStore.open(inp, emb.model, emb.dim)
    assert await store.index_source_async(CHUNKS, emb) == 6
    assert (tmp_path / "novel_memory" / "vectors.npy").exists()

    # 다시 열어 같은 원문을 인덱싱하면 임베딩 호출이 없다
    store2 = TranslationMemoryStore.open(inp, emb.model, emb.dim)
    assert len(store2.items) == 6
    assert await store2.index_source_async(CHUNKS, emb) == 0
    assert len(emb.calls) == 1

    # 문단 하나만 바뀌면 그 문단만 임베딩한다
    changed = CHUNKS[:2] + ["魔王城の門が開いた。赤い霧が流れ出してくる。\n\n勇者リリアは剣を握りしめた。「ここで終わらせる」"]
    assert await store2.index_source_async(changed, emb) == 1

    # 모델이 바뀌면 새로 만든다
    other = FakeEmbedder(model="fake-2")
    store3 = TranslationMemoryStore.open(inp, other.model, other.dim)
    assert store3.items == []


@pytest.mark.asyncio
async def test_record_and_search_examples(tmp_path):
    emb = FakeEmbedder()
    store = TranslationMemoryStore.open(tmp_path / "novel.txt", emb.model, emb.dim)
    await store.index_source_async(CHUNKS, emb)

    # 번역된 문단이 없으면 결과 없음
    assert store.search_examples(CHUNKS[1], top_k=3, min_similarity=0.0) == []

    assert store.record_translation(0, CHUNKS[0], "용사 릴리아는 검을 뽑았다. “기다려라, 마왕.”\n\n밤의 숲은 고요했다. 달이 구름에 가려져 있다.")
    # 문단 수가 다르면 기록하지 않는다
    assert not store.record_translation(2, CHUNKS[2], "한 문단으로 합쳐진 번역")
    assert store.stats["mismatched_chunks"] == 1

    examples = store.search_examples(CHUNKS[1], top_k=1, min_similarity=0.3)
    assert len(examples) == 1
    assert examples[0].translation.startswith("용사 릴리아는 검을 뽑았다")
    assert examples[0].chunk_index == 0

    # 청크 자신의 문단은 예시로 쓰지 않는다
    assert all(e.chunk_index != 0 for e in store.search_examples(CHUNKS[0], top_k=5, min_similarity=0.0))
    # 기준값을 넘지 못하면 제외
    assert store.search_examples(CHUNKS[1], top_k=3, min_similarity=0.999) == []
    # 인덱싱되지 않은 텍스트(분할 재시도 조각 등)는 결과 없음
    assert store.search_examples("인덱싱되지 않은 전혀 다른 텍스트입니다.", top_k=3, min_similarity=0.0) == []

    block = store.format_examples(examples)
    assert block.startswith("<translation_memory>") and '<example chunk="1">' in block

    # 번역문은 다시 열어도 유지되고, 원문 재인덱싱에도 보존된다
    reopened = TranslationMemoryStore.open(tmp_path / "novel.txt", emb.model, emb.dim)
    await reopened.index_source_async(CHUNKS, emb)
    assert reopened.summary()["translated"] == 2


@pytest.mark.asyncio
async def test_search_glossary(tmp_path):
    emb = FakeEmbedder()
    store = TranslationMemoryStore.open(tmp_path / "novel.txt", emb.model, emb.dim)
    await store.index_source_async(CHUNKS, emb)
    await store.index_glossary_async([
        GlossaryEntryDTO(keyword="勇者リリア", translated_keyword="용사 릴리아", target_language="ko", occurrence_count=3),
        GlossaryEntryDTO(keyword="市場", translated_keyword="시장", target_language="ko", occurrence_count=1),
    ], emb)
    assert store.summary()["glossary"] == 2
    hits = store.search_glossary(CHUNKS[2], top_k=1, min_similarity=0.0)
    assert hits == ["勇者リリア"]
