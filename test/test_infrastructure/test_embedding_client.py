"""
Unit tests for VoyageEmbeddingClient (requests.post 모의).
"""

import unittest
from unittest.mock import MagicMock, patch

import requests

from core.exceptions import BtgApiClientException, BtgApiInvalidRequestException, BtgApiRateLimitException
from infrastructure.embedding_client import (
    VoyageEmbeddingClient,
    batch_texts,
    create_embedding_client,
)


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = str(payload)
    return r


def _ok(n, dim=4, offset=0):
    return _resp(payload={
        "data": [{"index": i, "embedding": [float(offset + i)] * dim} for i in reversed(range(n))],  # 순서가 섞여 와도 index로 맞춘다
        "usage": {"total_tokens": 10 * n},
    })


class TestBatching(unittest.TestCase):
    def test_batch_by_count_and_tokens(self):
        self.assertEqual(batch_texts(["a"] * 5, max_count=2, max_tokens=10_000), [[0, 1], [2, 3], [4]])
        # 토큰 예산은 한도의 80%: 100 → 80자
        self.assertEqual(batch_texts(["x" * 50, "y" * 50, "z" * 10], max_count=100, max_tokens=100), [[0], [1, 2]])
        self.assertEqual(batch_texts([], 10, 10), [])


class TestVoyageEmbeddingClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = VoyageEmbeddingClient("vk-test", model="voyage-4-lite", output_dimension=512, max_retries=2)

    def test_requires_key(self):
        with self.assertRaises(BtgApiClientException):
            VoyageEmbeddingClient("")

    @patch("infrastructure.embedding_client.requests.post")
    async def test_embed_documents_payload_and_order(self, post):
        post.return_value = _ok(3)
        vectors = await self.client.embed_documents(["가", "나", "다"])
        self.assertEqual([v[0] for v in vectors], [0.0, 1.0, 2.0])

        url = post.call_args[0][0]
        kwargs = post.call_args[1]
        self.assertEqual(url, "https://api.voyageai.com/v1/embeddings")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer vk-test")
        self.assertEqual(kwargs["json"], {
            "input": ["가", "나", "다"], "model": "voyage-4-lite", "truncation": True,
            "input_type": "document", "output_dimension": 512,
        })

    @patch("infrastructure.embedding_client.requests.post")
    async def test_splits_into_requests_of_1000(self, post):
        post.side_effect = [_ok(1000), _ok(1, offset=1000)]
        vectors = await self.client.embed_queries(["t"] * 1001)
        self.assertEqual(len(vectors), 1001)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0][1]["json"]["input_type"], "query")
        self.assertEqual(vectors[1000][0], 1000.0)

    @patch("infrastructure.embedding_client.time.sleep")
    @patch("infrastructure.embedding_client.requests.post")
    async def test_retries_429_and_5xx(self, post, sleep):
        post.side_effect = [_resp(429, {"detail": "rate"}), _resp(503, {"detail": "busy"}), _ok(1)]
        vectors = await self.client.embed_documents(["a"])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(sleep.call_count, 2)

    @patch("infrastructure.embedding_client.time.sleep")
    @patch("infrastructure.embedding_client.requests.post")
    async def test_gives_up_after_max_retries(self, post, sleep):
        post.return_value = _resp(429, {"detail": "rate"})
        with self.assertRaises(BtgApiRateLimitException):
            await self.client.embed_documents(["a"])
        self.assertEqual(post.call_count, 3)

    @patch("infrastructure.embedding_client.time.sleep")
    @patch("infrastructure.embedding_client.requests.post")
    async def test_auth_and_bad_request_not_retried(self, post, sleep):
        post.return_value = _resp(401, {"detail": "bad key"})
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.embed_documents(["a"])
        self.assertIn("API 키", str(ctx.exception))
        post.return_value = _resp(400, {"detail": "bad"})
        with self.assertRaises(BtgApiInvalidRequestException):
            await self.client.embed_documents(["a"])
        sleep.assert_not_called()

    @patch("infrastructure.embedding_client.time.sleep")
    @patch("infrastructure.embedding_client.requests.post")
    async def test_network_error_retried(self, post, sleep):
        post.side_effect = [requests.exceptions.ConnectionError("down"), _ok(1)]
        self.assertEqual(len(await self.client.embed_documents(["a"])), 1)

    @patch("infrastructure.embedding_client.requests.post")
    async def test_count_mismatch_raises(self, post):
        post.return_value = _ok(1)
        with self.assertRaises(BtgApiClientException):
            await self.client.embed_documents(["a", "b"])

    @patch("infrastructure.embedding_client.requests.post")
    async def test_health_check(self, post):
        post.return_value = _ok(1, dim=512)
        ok, msg = await self.client.check_health_async()
        self.assertTrue(ok)
        self.assertIn("512차원", msg)
        post.return_value = _resp(401, {"detail": "bad key"})
        ok, msg = await self.client.check_health_async()
        self.assertFalse(ok)

    def test_factory(self):
        c = create_embedding_client({"voyage_api_key": "k", "voyage_model": "voyage-4", "voyage_output_dimension": 1024})
        self.assertEqual((c.model, c.output_dimension), ("voyage-4", 1024))
        with self.assertRaises(BtgApiClientException):
            create_embedding_client({"embedding_provider": "other", "voyage_api_key": "k"})


if __name__ == "__main__":
    unittest.main()
