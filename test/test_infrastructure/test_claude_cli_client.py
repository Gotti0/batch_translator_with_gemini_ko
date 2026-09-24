"""
Claude CLI Client Unit Tests
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import BtgApiClientException, BtgApiRateLimitException
from infrastructure.claude_cli_client import ClaudeCliClient


class TestClaudeCliClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ClaudeCliClient(cli_path="claude", model_name="claude-3-5-sonnet-20241022", timeout_seconds=10)

    def test_properties(self):
        self.assertEqual(self.client.provider_name, "claude_cli")
        self.assertFalse(self.client.supports_pagefold)

    def test_prepare_prompt_string(self):
        # String prompt
        self.assertEqual(self.client._prepare_prompt_string("hello"), "hello")

        # Multiturn list prompt
        multiturn = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"}
        ]
        res = self.client._prepare_prompt_string(multiturn)
        self.assertIn("[USER]:\nhi", res)
        self.assertIn("[ASSISTANT]:\nhello there", res)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_success_json(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        response_json = json.dumps({
            "is_error": False,
            "result": "안녕하세요, 세계!"
        })
        mock_proc.communicate.return_value = (response_json.encode("utf-8"), b"")
        mock_subprocess.return_value = mock_proc

        result = await self.client.generate_text_async("Hello world", system_instruction="Translate to Korean")
        self.assertEqual(result, "안녕하세요, 세계!")
        mock_subprocess.assert_called_once()
        args = mock_subprocess.call_args[0]
        self.assertIn("-p", args)
        self.assertIn("--tools", args)
        self.assertIn("--model", args)
        self.assertIn("claude-3-5-sonnet-20241022", args)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_rate_limit(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"Rate limit exceeded. Please try again later.")
        mock_subprocess.return_value = mock_proc

        with self.assertRaises(BtgApiRateLimitException):
            await self.client.generate_text_async("Hello")

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_timeout(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_subprocess.return_value = mock_proc

        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("Hello")
        self.assertIn("시간 초과", str(ctx.exception))

    async def test_list_models_async_from_cache_or_fallback(self):
        models = await self.client.list_models_async()
        self.assertIn("default", models)
        self.assertTrue(len(models) > 1)

    @patch("os.path.exists", return_value=False)
    async def test_list_models_async_fallback(self, mock_exists):
        models = await self.client.list_models_async()
        self.assertIn("claude-sonnet-5", models)
        self.assertIn("default", models)


if __name__ == "__main__":
    unittest.main()
