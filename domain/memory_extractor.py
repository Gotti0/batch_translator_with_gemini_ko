"""
Memory Extractor (인물 메모 추출) for Neo Batch Translator (BTG)

번역이 끝난 청크(원문 + 번역문)에서 인물·고유명사와 "이후 번역 일관성에 필요한 사실"
(말투, 호칭, 1인칭, 관계)을 뽑아 기억 그래프의 성장 가지로 넣는다.

WygLore Leaf의 투 트랙 라우팅: 번역은 좋은 모델이, 추출은 저렴한 모델이 백그라운드로 맡는다.
추출 실패는 번역에 영향을 주지 않는다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

MAX_SOURCE_CHARS = 8000


class ExtractedEntity(BaseModel):
    name: str = Field(description="원문 표기 이름")
    aliases: List[str] = Field(default_factory=list, description="원문에 나온 별칭·다른 표기")
    translated_name: str = Field(default="", description="번역문에서 쓴 이름")
    category: str = Field(default="character", description="character / place / item / other")
    note: str = Field(default="", description="번역 일관성에 필요한 사실 (한국어 80자 이내)")


EXTRACTION_PROMPT = """다음은 소설 원문 한 부분과 그 한국어 번역입니다.
이후 번역에서 일관성을 지키는 데 필요한 인물(및 중요한 고유명사)을 JSON 배열로 추출하세요.

각 항목:
- name: 원문 표기
- aliases: 원문에 나온 별칭·애칭·다른 표기 (없으면 빈 배열)
- translated_name: 번역문에서 쓴 이름
- category: character / place / item / other
- note: 한국어 80자 이내. 번역문 기준으로 이 인물의 말투(존댓말·반말), 1인칭, 다른 인물을 부르는 호칭,
  드러난 관계처럼 이후 번역에 필요한 사실만 적으세요. 줄거리 요약은 쓰지 마세요.

이미 알려진 인물: {known}
알려진 인물은 새로 드러난 사실이 있을 때만 포함하세요. 해당 항목이 없으면 빈 배열 []을 반환하세요.

<source>
{source}
</source>
<translation>
{translation}
</translation>"""


def _to_dicts(response: Any) -> List[Dict[str, Any]]:
    """프로바이더마다 다른 응답(Pydantic 목록, dict 목록, JSON 문자열)을 dict 목록으로 맞춘다."""
    if response is None:
        return []
    if isinstance(response, str):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
        try:
            response = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"인물 메모 추출 응답을 JSON으로 해석하지 못했습니다: {text[:200]}")
            return []
    if isinstance(response, dict):
        response = response.get("entities") or response.get("items") or [response]
    rows = []
    for item in response if isinstance(response, list) else []:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            rows.append(item)
    return rows


def find_excerpt(source: str, names: Iterable[str], limit: int = 120) -> str:
    """원문 닻: 이름이 처음 나오는 줄."""
    for line in source.splitlines():
        if any(n and n in line for n in names):
            return line.strip()[:limit]
    return ""


class MemoryExtractor:
    """저렴한 LLM으로 인물 메모를 추출한다."""

    def __init__(self, llm_client: Any, model_name: str) -> None:
        self.llm_client = llm_client
        self.model_name = model_name

    async def extract(self, source: str, translation: str, known_names: Iterable[str] = ()) -> List[ExtractedEntity]:
        known = ", ".join(sorted(set(known_names))[:50]) or "없음"
        prompt = EXTRACTION_PROMPT.format(
            known=known, source=source[:MAX_SOURCE_CHARS], translation=translation[:MAX_SOURCE_CHARS]
        )
        response = await self.llm_client.generate_text_async(
            prompt=prompt,
            model_name=self.model_name,
            generation_config_dict={
                "temperature": 0.2,
                "response_mime_type": "application/json",
                "response_schema": list[ExtractedEntity],
            },
        )
        entities = []
        for row in _to_dicts(response):
            try:
                entities.append(ExtractedEntity(**{k: v for k, v in row.items() if k in ExtractedEntity.model_fields}))
            except Exception as e:
                logger.debug(f"추출 항목을 건너뜁니다 ({row}): {e}")
        return entities
