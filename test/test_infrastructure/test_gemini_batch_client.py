"""
Unit tests for GeminiBatchClient (SDK의 client.aio.batches / files를 모의 객체로 대체).
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
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
        self.sdk.aio.models.count_tokens = AsyncMock(return_value=t.CountTokensResponse(total_tokens=123))
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
        self.assertEqual(info.estimated_input_tokens, 123)

    async def test_token_estimate_includes_system_and_multimodal_content(self):
        part = t.Part.from_uri(file_uri="https://example.invalid/file.pdf", mime_type="application/pdf")
        req = t.InlinedRequest(contents=[t.Content(parts=[part, t.Part(text="source")])],
                               config=t.GenerateContentConfig(system_instruction="translate faithfully"))
        self.sdk.aio.models.count_tokens.side_effect = [t.CountTokensResponse(total_tokens=100), t.CountTokensResponse(total_tokens=200)]
        self.assertEqual(await self.client.estimate_input_tokens("model", [req, req]), 300)
        args = self.sdk.aio.models.count_tokens.call_args.kwargs
        self.assertEqual(args["contents"][0], "translate faithfully")
        self.assertEqual(args["contents"][1].parts[0], part)
        self.assertEqual(args["config"].http_options.retry_options.attempts, 1)

    async def test_failed_count_does_not_prevent_submit_or_claim_zero(self):
        self.sdk.aio.models.count_tokens.side_effect = _api_error(429, "quota")
        self.sdk.aio.batches.create.return_value = t.BatchJob(name="batches/1", state=t.JobState.JOB_STATE_PENDING)
        req = t.InlinedRequest(contents=[t.Content(parts=[t.Part(text="source")])])
        with self.assertLogs("infrastructure.gemini_batch_client", level="WARNING") as logs:
            info = await self.client.submit("model", [req], "x")
        self.assertIsNone(info.estimated_input_tokens)
        self.sdk.aio.batches.create.assert_awaited_once()
        self.assertIn("UNKNOWN", " ".join(logs.output))

    async def test_cancel_during_count_prevents_submission(self):
        self.sdk.aio.models.count_tokens.side_effect = asyncio.CancelledError()
        req = t.InlinedRequest(contents=[t.Content(parts=[t.Part(text="source")])])
        with self.assertRaises(asyncio.CancelledError):
            await self.client.submit("model", [req], "x")
        self.sdk.aio.batches.create.assert_not_awaited()

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
        self.assertIn("billing required", str(ctx.exception))
        self.assertNotIn("무료 티어 키", str(ctx.exception))

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


class TestQuotaDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_submit_records_all_quota_limits_and_context_without_request_content(self):
        # 서버 형식을 따르는 합성 응답이며 실제 프로젝트의 한도나 측정값이 아니다.
        cases = [
            ("GenerateContentRequestsPerMinute", "requests", "RPM", "minute"),
            ("GenerateContentRequestsPerDay", "requests", "RPD", "day"),
            ("GenerateContentInputTokensPerMinute", "input_tokens", "TPM", "minute"),
            ("GenerateContentInputTokensPerDay", "input_tokens", "TPD", "day"),
            ("BatchEnqueuedTokensPerModelPerDay", "batch_enqueued_tokens", "BATCH_ENQUEUED_TOKENS", "day"),
            ("InlineBatchRequestsPerMinute", "inline_batch_requests", "INLINE_BATCH", "minute"),
            ("ConcurrentBatchRequests", "batch_requests", "BATCH_OTHER", "unspecified"),
            ("NewQuotaName", "new_metric", "UNKNOWN", "unspecified"),
        ]
        violations = [
            {"quotaId": quota_id, "quotaMetric": f"generativelanguage.googleapis.com/{metric}",
             "quotaValue": "100", "quotaDimensions": {"model": "test-model", "location": "global"}}
            for quota_id, metric, _, _ in cases
        ]
        error = genai_errors.ClientError(429, {"error": {
            "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": violations},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"},
            ],
        }}, response=httpx.Response(429, headers={
            "Retry-After": "13", "X-Request-ID": "request-123", "X-Goog-Request-ID": "google-456",
            "Set-Cookie": "private-cookie", "x-goog-api-key": "private-response-key",
        }))
        sdk = MagicMock()
        sdk.aio.batches.create = AsyncMock(side_effect=error)
        client = GeminiBatchClient("private-api-key", sdk_client=sdk)
        requests = [t.InlinedRequest(contents=[t.Content(parts=[t.Part(text="private-source-text")])])]
        with self.assertLogs("infrastructure.gemini_batch_client", level="ERROR") as logs:
            with self.assertRaises(BtgApiRateLimitException) as raised:
                await client.submit("test-model", requests, "private-file-title")
        diagnostic = json.loads(logs.records[0].getMessage().split("Gemini 429 진단: ", 1)[1])
        self.assertEqual(diagnostic["model"], "test-model")
        self.assertEqual(diagnostic["input_mode"], "inline")
        self.assertEqual(diagnostic["request_count"], 1)
        self.assertEqual(diagnostic["key_fingerprint"], key_fingerprint("private-api-key"))
        self.assertEqual(diagnostic["retry_delay"], "12.5s")
        self.assertEqual(diagnostic["response_headers"], {
            "retry-after": "13", "x-request-id": "request-123", "x-goog-request-id": "google-456",
        })
        for actual, (quota_id, _, category, period) in zip(diagnostic["violations"], cases):
            self.assertEqual(actual["type"], category)
            self.assertEqual(actual["period"], period)
            self.assertEqual(actual["quotaValue"], "100")
            self.assertEqual(actual["quotaDimensions"]["model"], "test-model")
            self.assertIn(quota_id, raised.exception.message)
        self.assertEqual(len(diagnostic["violations"]), len(cases))
        self.assertIs(raised.exception.original_exception, error)
        for secret in ("private-api-key", "private-source-text", "private-file-title", "private-cookie", "private-response-key"):
            self.assertNotIn(secret, " ".join(logs.output))

    async def test_help_only_429_stays_unknown_and_does_not_claim_free_tier(self):
        sdk = MagicMock()
        sdk.aio.batches.create = AsyncMock(side_effect=genai_errors.ClientError(429, {"error": {
            "status": "RESOURCE_EXHAUSTED",
            "message": "You exceeded your current quota, please check your plan and billing details.",
            "details": [{"@type": "type.googleapis.com/google.rpc.Help", "links": []}],
        }}))
        with self.assertLogs("infrastructure.gemini_batch_client", level="ERROR") as logs:
            with self.assertRaises(BtgApiRateLimitException) as raised:
                await GeminiBatchClient("key", sdk_client=sdk).submit("m", [], "x")
        diagnostic = json.loads(logs.records[0].getMessage().split("Gemini 429 진단: ", 1)[1])
        self.assertEqual(diagnostic["quota_types"], ["UNKNOWN"])
        self.assertEqual(diagnostic["violations"], [])
        self.assertIn("서버가 할당량 식별자를 제공하지 않음", raised.exception.message)
        self.assertNotIn("무료 티어 키", raised.exception.message)

    async def test_polling_uses_error_info_and_does_not_invent_submission_context(self):
        sdk = MagicMock()
        sdk.aio.batches.get = AsyncMock(side_effect=genai_errors.ClientError(429, {"error": {
            "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded",
            "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                         "reason": "RATE_LIMIT_EXCEEDED", "domain": "googleapis.com",
                         "metadata": {"quota_metric": "read_requests", "quota_limit": "ReadRequestsPerMinute",
                                      "quota_limit_value": "60", "api_key": "private-key"}}],
        }}))
        with self.assertLogs("infrastructure.gemini_batch_client", level="ERROR") as logs:
            with self.assertRaises(BtgApiRateLimitException) as raised:
                await GeminiBatchClient("key", sdk_client=sdk).get("batches/1")
        diagnostic = json.loads(logs.records[0].getMessage().split("Gemini 429 진단: ", 1)[1])
        self.assertEqual(diagnostic["action"], "조회")
        self.assertEqual(diagnostic["quota_types"], ["RPM"])
        self.assertEqual(diagnostic["error_info"][0]["reason"], "RATE_LIMIT_EXCEEDED")
        self.assertNotIn("input_mode", diagnostic)
        self.assertNotIn("private-key", " ".join(logs.output))
        self.assertIn("ReadRequestsPerMinute", raised.exception.message)

    def test_malformed_details_preserve_original_429(self):
        client = GeminiBatchClient("key", sdk_client=MagicMock())
        for details in (None, [], {"error": None}, {"error": {"details": "invalid"}},
                        {"error": {"details": [None,
                         {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": 42},
                         {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "metadata": 42}]}}):
            with self.subTest(details=details):
                error = Exception("429")
                error.code = 429
                error.details = details
                error.response = SimpleNamespace(headers=None)
                with self.assertLogs("infrastructure.gemini_batch_client", level="ERROR"):
                    wrapped = client._wrap_error("제출", error)
                self.assertIsInstance(wrapped, BtgApiRateLimitException)
                self.assertIs(wrapped.original_exception, error)
                self.assertIn("UNKNOWN", wrapped.message)
