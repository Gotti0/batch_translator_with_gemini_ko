"""
Embedding Client for Neo Batch Translator (BTG)

번역 장기기억(translation memory)에 쓰는 임베딩 API 클라이언트입니다.
현재는 Voyage AI(`POST /v1/embeddings`)만 구현하며, 공식 SDK 대신 REST를 직접 호출합니다.
"""

from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.exceptions import (
    BtgApiClientException,
    BtgApiInvalidRequestException,
    BtgApiRateLimitException,
)
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MAX_TEXTS_PER_REQUEST = 1000
# 요청당 토큰 한도 (https://docs.voyageai.com/docs/embeddings). 목록에 없는 모델은 가장 작은 값을 쓴다.
VOYAGE_TOKEN_LIMITS = {
    "voyage-4-lite": 1_000_000,
    "voyage-3.5-lite": 1_000_000,
    "voyage-4": 320_000,
    "voyage-3.5": 320_000,
    "voyage-2": 320_000,
}
VOYAGE_DEFAULT_TOKEN_LIMIT = 120_000
VOYAGE_MODELS = ["voyage-4-lite", "voyage-4", "voyage-4-large"]


class BaseEmbeddingClient(ABC):
    """문서·쿼리 텍스트를 벡터로 바꾸는 클라이언트."""

    model: str
    output_dimension: Optional[int]

    @abstractmethod
    async def embed_async(self, texts: List[str], input_type: Optional[str] = None) -> List[List[float]]:
        """텍스트 목록을 같은 순서의 벡터 목록으로 바꾼다."""

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embed_async(texts, input_type="document")

    async def embed_queries(self, texts: List[str]) -> List[List[float]]:
        return await self.embed_async(texts, input_type="query")

    async def check_health_async(self) -> Tuple[bool, str]:
        try:
            vectors = await self.embed_async(["연결 테스트"], input_type="query")
            return True, f"{self.provider_name} 연결 성공 (모델 {self.model}, {len(vectors[0])}차원)"
        except Exception as e:
            return False, f"{self.provider_name} 연결 실패: {e}"

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


def _retryable(error: BtgApiClientException) -> BtgApiClientException:
    """429·5xx·네트워크 오류처럼 다시 보내면 될 수 있는 오류로 표시한다."""
    error.retryable = True  # type: ignore[attr-defined]
    return error


def batch_texts(texts: List[str], max_count: int, max_tokens: int) -> List[List[int]]:
    """요청당 개수·토큰 한도를 넘지 않게 인덱스를 묶는다.

    토큰 수는 글자 수로 보수적으로 추정한다 (CJK는 대략 글자당 1토큰 이하).
    """
    budget = int(max_tokens * 0.8)
    groups: List[List[int]] = []
    current: List[int] = []
    current_tokens = 0
    for i, text in enumerate(texts):
        est = max(1, len(text))
        if current and (len(current) >= max_count or current_tokens + est > budget):
            groups.append(current)
            current, current_tokens = [], 0
        current.append(i)
        current_tokens += est
    if current:
        groups.append(current)
    return groups


class VoyageEmbeddingClient(BaseEmbeddingClient):
    """Voyage AI 텍스트 임베딩 REST 클라이언트."""

    def __init__(
        self,
        api_key: str,
        model: str = "voyage-4-lite",
        output_dimension: Optional[int] = 512,
        timeout_seconds: float = 60.0,
        max_retries: int = 4,
        base_url: str = VOYAGE_EMBEDDINGS_URL,
    ) -> None:
        if not api_key or not api_key.strip():
            raise BtgApiClientException("Voyage API 키가 설정되지 않았습니다.")
        self.api_key = api_key.strip()
        self.model = (model or "voyage-4-lite").strip()
        self.output_dimension = int(output_dimension) if output_dimension else None
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url

    @property
    def provider_name(self) -> str:
        return "voyage"

    @property
    def token_limit(self) -> int:
        return VOYAGE_TOKEN_LIMITS.get(self.model, VOYAGE_DEFAULT_TOKEN_LIMIT)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _raise_for_status(self, response: requests.Response) -> None:
        if response.status_code == 200:
            return
        try:
            body = response.json()
            message = body.get("detail") or body.get("message") or body.get("error") or response.text
        except ValueError:
            message = response.text
        status = response.status_code
        if status == 429:
            raise _retryable(BtgApiRateLimitException(f"Voyage 사용량 한도 초과 (429): {message}"))
        if status in (401, 403):
            raise BtgApiClientException(f"Voyage 인증 실패 ({status}): API 키를 확인하세요. {message}")
        if status == 400:
            raise BtgApiInvalidRequestException(f"Voyage 잘못된 요청 (400): {message}")
        error = BtgApiClientException(f"Voyage 요청 실패 ({status}): {message}")
        raise _retryable(error) if status >= 500 else error

    def _post_once(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.post(self.base_url, headers=self._headers(), json=payload, timeout=self.timeout_seconds)
        except requests.exceptions.Timeout as e:
            raise _retryable(BtgApiClientException(f"Voyage 응답 시간 초과 ({self.timeout_seconds:.0f}초)")) from e
        except requests.exceptions.RequestException as e:
            raise _retryable(BtgApiClientException(f"Voyage 요청 중 네트워크 오류: {e}")) from e
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as e:
            raise BtgApiClientException(f"Voyage 응답을 JSON으로 해석할 수 없습니다: {response.text[:200]}") from e

    def _post_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """429·5xx는 지수 백오프로 재시도한다. 인증·요청 오류는 바로 올린다."""
        attempt = 0
        while True:
            try:
                return self._post_once(payload)
            except BtgApiClientException as e:
                if not getattr(e, "retryable", False) or attempt >= self.max_retries:
                    raise
                delay = min(30.0, 2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(f"Voyage 재시도 {attempt + 1}/{self.max_retries} ({delay:.1f}초 후): {e}")
                time.sleep(delay)
                attempt += 1

    def _embed_sync(self, texts: List[str], input_type: Optional[str]) -> List[List[float]]:
        vectors: List[Optional[List[float]]] = [None] * len(texts)
        total_tokens = 0
        for group in batch_texts(texts, VOYAGE_MAX_TEXTS_PER_REQUEST, self.token_limit):
            payload: Dict[str, Any] = {"input": [texts[i] for i in group], "model": self.model, "truncation": True}
            if input_type:
                payload["input_type"] = input_type
            if self.output_dimension:
                payload["output_dimension"] = self.output_dimension
            data = self._post_with_retry(payload)
            items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
            if len(items) != len(group):
                raise BtgApiClientException(f"Voyage 응답 개수 불일치: 요청 {len(group)}개, 응답 {len(items)}개")
            for local_i, item in enumerate(items):
                vectors[group[local_i]] = item["embedding"]
            total_tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
        logger.info(f"Voyage 임베딩 완료: {len(texts)}개, {total_tokens} 토큰 (모델 {self.model})")
        return vectors  # type: ignore[return-value]

    async def embed_async(self, texts: List[str], input_type: Optional[str] = None) -> List[List[float]]:
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._embed_sync(list(texts), input_type))


def create_embedding_client(config: Dict[str, Any]) -> BaseEmbeddingClient:
    """설정으로 임베딩 클라이언트를 만든다."""
    provider = str(config.get("embedding_provider") or "voyage").lower()
    if provider != "voyage":
        raise BtgApiClientException(f"지원하지 않는 임베딩 프로바이더입니다: {provider}")
    return VoyageEmbeddingClient(
        api_key=str(config.get("voyage_api_key") or ""),
        model=str(config.get("voyage_model") or "voyage-4-lite"),
        output_dimension=config.get("voyage_output_dimension", 512),
    )
