"""
Codex CLI Client Unit Tests
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import BtgApiClientException, BtgApiRateLimitException
from infrastructure.codex_cli_client import CodexCliClient


class TestCodexCliClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = CodexCliClient(cli_path="codex", model_name="gpt-5.5", timeout_seconds=10)

    def test_properties(self):
        self.assertEqual(self.client.provider_name, "codex_cli")
        self.assertFalse(self.client.supports_pagefold)

    def test_prepare_prompt_string(self):
        # Plain prompt
        self.assertEqual(self.client._prepare_prompt_string("hello"), "hello")

        # Prompt with system instruction
        res = self.client._prepare_prompt_string("hello", system_instruction="Translate to Korean")
        self.assertIn("System Instructions:\nTranslate to Korean", res)
        self.assertIn("User Input:\nhello", res)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_success_jsonl(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        
        jsonl_output = "\n".join([
            '{"type":"thread.started"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"안녕하세요, 반갑습니다."}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}'
        ])
        mock_proc.communicate.return_value = (jsonl_output.encode("utf-8"), b"")
        mock_subprocess.return_value = mock_proc

        result = await self.client.generate_text_async("Hello", system_instruction="Translate")
        self.assertEqual(result, "안녕하세요, 반갑습니다.")
        mock_subprocess.assert_called_once()
        args = mock_subprocess.call_args[0]
        self.assertIn("exec", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--json", args)
        self.assertIn("-m", args)
        self.assertIn("gpt-5.5", args)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_with_schema(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()
        json_str = '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"ko\\": \\"안녕\\"}"}}'
        mock_proc.communicate.return_value = (
            json_str.encode("utf-8"),
            b""
        )
        mock_subprocess.return_value = mock_proc

        schema = {"type": "object", "properties": {"ko": {"type": "string"}}}
        result = await self.client.generate_text_async("Hello", response_schema=schema)
        self.assertEqual(result, '{"ko": "안녕"}')
        args = mock_subprocess.call_args[0]
        self.assertIn("--output-schema", args)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_rate_limit(self, mock_subprocess):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.kill = MagicMock()
        mock_proc.communicate.return_value = (b"", b"Rate limit reached. Too many requests.")
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

    async def test_list_models_async(self):
        models = await self.client.list_models_async()
        self.assertTrue(len(models) > 0)
        self.assertIn("default", models)


if __name__ == "__main__":
    unittest.main()
