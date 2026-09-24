"""
Unit tests for GeminiBatchClient (SDK의 client.aio.batches / files를 모의 객체로 대체).
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from google.genai import errors as genai_errors
from google.genai import types as t

from core.exceptions import (
    BtgApiClientException,
    BtgApiInvalidRequestException,
    BtgApiRateLimitException,
)
from infrastructure.gemini_batch_client import (
    BatchJobState,
    GeminiBatchClient,
    key_fingerprint,
    map_job_state,
    parse_inlined_responses,
)


def _response(text=None, finish=t.FinishReason.STOP, thought=None, block_reason=None):
    parts = []
    if thought:
        parts.append(t.Part(text=thought, thought=True))
    if text is not None:
        parts.append(t.Part(text=text))
    kwargs = {"candidates": [t.Candidate(content=t.Content(role="model", parts=parts), finish_reason=finish)]}
    if block_reason:
        kwargs = {"prompt_feedback": t.GenerateContentResponsePromptFeedback(block_reason=block_reason)}
    return t.GenerateContentResponse(**kwargs)


def _api_error(code, message="boom"):
    return genai_errors.APIError(code, {"error": {"code": code, "message": message, "status": "X"}})


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class TestParsing(unittest.TestCase):
    def test_map_job_state(self):
        self.assertEqual(map_job_state(t.JobState.JOB_STATE_PENDING), BatchJobState.PENDING)
        self.assertEqual(map_job_state("JOB_STATE_RUNNING"), BatchJobState.RUNNING)
        self.assertEqual(map_job_state(t.JobState.JOB_STATE_SUCCEEDED), BatchJobState.SUCCEEDED)
        self.assertEqual(map_job_state(t.JobState.JOB_STATE_EXPIRED), BatchJobState.EXPIRED)
        self.assertEqual(map_job_state(t.JobState.JOB_STATE_PARTIALLY_SUCCEEDED), BatchJobState.PARTIALLY_SUCCEEDED)
        self.assertEqual(map_job_state(None), BatchJobState.UNKNOWN)
        self.assertTrue(BatchJobState.EXPIRED.is_terminal)
        self.assertFalse(BatchJobState.RUNNING.is_terminal)

    def test_parse_inlined_responses(self):
        items = [
            t.InlinedResponse(metadata={"key": "chunk-00000"}, response=_response("번역", thought="생각 중")),
            t.InlinedResponse(metadata={"key": "chunk-00001"}, response=_response(None, finish=t.FinishReason.PROHIBITED_CONTENT)),
            t.InlinedResponse(metadata={"key": "chunk-00002"}, response=_response(block_reason=t.BlockedReason.SAFETY)),
            t.InlinedResponse(metadata={"key": "chunk-00003"}, error=t.JobError(code=500, message="internal")),
            t.InlinedResponse(metadata={"key": "chunk-00004"}, response=_response("   ")),
        ]
        results = parse_inlined_responses(items)

        self.assertEqual(results[0].key, "chunk-00000")
        self.assertEqual(results[0].text, "번역")  # 사고 파트 제외
        self.assertTrue(results[1].blocked)
        self.assertEqual(results[1].finish_reason, "PROHIBITED_CONTENT")
        self.assertTrue(results[2].blocked)
        self.assertIn("SAFETY", results[2].error)
        self.assertFalse(results[3].blocked)
        self.assertIn("internal", results[3].error)
        self.assertTrue(results[4].blocked)
        self.assertIsNone(results[4].text)

    def test_fingerprint_hides_key(self):
        fp = key_fingerprint("AIza-secret")
        self.assertTrue(fp.startswith("sha256:"))
        self.assertNotIn("secret", fp)
        self.assertEqual(fp, key_fingerprint("AIza-secret"))
        self.assertNotEqual(fp, key_fingerprint("AIza-other"))


class TestGeminiBatchClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sdk = MagicMock()
        self.sdk.aio.batches.create = AsyncMock()
        self.sdk.aio.batches.get = AsyncMock()
        self.sdk.aio.batches.cancel = AsyncMock()
        self.sdk.aio.batches.list = AsyncMock()
        self.sdk.aio.files.upload = AsyncMock()
        self.client = GeminiBatchClient("key", sdk_client=self.sdk)

    def test_requires_key(self):
        with self.assertRaises(BtgApiClientException):
            GeminiBatchClient("", sdk_client=self.sdk)

    async def test_submit(self):
        self.sdk.aio.batches.create.return_value = t.BatchJob(
            name="batches/1", display_name="btg-x-r1-1", state=t.JobState.JOB_STATE_PENDING
        )
        reqs = [t.InlinedRequest(contents=[t.Content(role="user", parts=[t.Part(text="a")])], metadata={"key": "chunk-00000"})]
        info = await self.client.submit("gemini-3.8-flash", reqs, "btg-x-r1-1")

        self.assertEqual(info.name, "batches/1")
        self.assertEqual(info.state, BatchJobState.PENDING)
        self.assertIsNone(info.results)
        kwargs = self.sdk.aio.batches.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        self.assertIs(kwargs["src"], reqs)
        self.assertEqual(kwargs["config"].display_name, "btg-x-r1-1")

    async def test_get_terminal_job_parses_results(self):
        self.sdk.aio.batches.get.return_value = t.BatchJob(
            name="batches/1",
            state=t.JobState.JOB_STATE_SUCCEEDED,
            completion_stats=t.CompletionStats(successful_count=1, failed_count=0),
            dest=t.BatchJobDestination(inlined_responses=[
                t.InlinedResponse(metadata={"key": "chunk-00007"}, response=_response("결과")),
            ]),
        )
        info = await self.client.get("batches/1")
        self.assertEqual(info.state, BatchJobState.SUCCEEDED)
        self.assertEqual(info.successful_count, 1)
        self.assertEqual([(r.key, r.text) for r in info.results], [("chunk-00007", "결과")])

    async def test_get_running_job_has_no_results(self):
        self.sdk.aio.batches.get.return_value = t.BatchJob(name="batches/1", state=t.JobState.JOB_STATE_RUNNING)
        info = await self.client.get("batches/1")
        self.assertEqual(info.state, BatchJobState.RUNNING)
        self.assertIsNone(info.results)

    async def test_error_mapping(self):
        cases = [(429, BtgApiRateLimitException), (404, BtgApiInvalidRequestException), (403, BtgApiClientException), (500, BtgApiClientException)]
        for code, exc in cases:
            self.sdk.aio.batches.get.side_effect = _api_error(code)
            with self.assertRaises(exc):
                await self.client.get("batches/1")
        self.sdk.aio.batches.create.side_effect = _api_error(403, "billing required")
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.submit("m", [], "x")
        self.assertIn("유료 키", str(ctx.exception))

    async def test_find_by_display_name(self):
        self.sdk.aio.batches.list.return_value = _AsyncIter([
            t.BatchJob(name="batches/a", display_name="other", state=t.JobState.JOB_STATE_RUNNING),
            t.BatchJob(name="batches/b", display_name="btg-x-r1-2", state=t.JobState.JOB_STATE_RUNNING),
        ])
        info = await self.client.find_by_display_name("btg-x-r1-2")
        self.assertEqual(info.name, "batches/b")

        self.sdk.aio.batches.list.return_value = _AsyncIter([])
        self.assertIsNone(await self.client.find_by_display_name("nope"))

    async def test_cancel_and_upload(self):
        await self.client.cancel("batches/1")
        self.sdk.aio.batches.cancel.assert_awaited_once_with(name="batches/1")

        self.sdk.aio.files.upload.return_value = t.File(name="files/abc", uri="https://x/files/abc", mime_type="application/pdf")
        uploaded = await self.client.upload_file(b"%PDF-1.7", "application/pdf", "glossary")
        self.assertEqual(uploaded.uri, "https://x/files/abc")
        cfg = self.sdk.aio.files.upload.call_args.kwargs["config"]
        self.assertEqual(cfg.mime_type, "application/pdf")


if __name__ == "__main__":
    unittest.main()


class TestFreeTierHint(unittest.IsolatedAsyncioTestCase):
    async def test_submit_rejection_mentions_free_tier(self):
        from infrastructure.gemini_batch_client import FREE_TIER_HINT
        sdk = MagicMock()
        sdk.aio.batches.create = AsyncMock(side_effect=_api_error(400, "Batch API is not available for free tier"))
        sdk.aio.batches.get = AsyncMock(side_effect=_api_error(403, "denied"))
        client = GeminiBatchClient("key", sdk_client=sdk)

        with self.assertRaises(BtgApiInvalidRequestException) as ctx:
            await client.submit("gemini-3.8-flash", [], "x")
        self.assertIn(FREE_TIER_HINT, str(ctx.exception))

        # 조회 실패에는 붙이지 않는다 (이미 제출된 작업이므로 티어 문제가 아님)
        with self.assertRaises(BtgApiClientException) as ctx:
            await client.get("batches/1")
        self.assertNotIn(FREE_TIER_HINT, str(ctx.exception))
