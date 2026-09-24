"""
Unit tests for AntigravityCliClient adapter.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
)
from infrastructure.antigravity_cli_client import AntigravityCliClient


class TestAntigravityCliClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AntigravityCliClient(cli_path="agy", model_name="gemini-3.8-flash-high")

    def test_initialization_properties(self):
        """기본 속성 초기화 검증"""
        self.assertEqual(self.client.provider_name, "antigravity_cli")
        self.assertFalse(self.client.supports_pagefold)
        self.assertEqual(self.client.model_name, "gemini-3.8-flash-high")

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_success_json(self, mock_exec):
        """정상 JSON 응답 파싱 검증"""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        response_data = {
            "status": "SUCCESS",
            "response": "번역된 텍스트 결과입니다.",
            "usage": {"total_tokens": 120},
        }
        mock_proc.communicate.return_value = (
            json.dumps(response_data, ensure_ascii=False).encode("utf-8"),
            b"",
        )
        mock_exec.return_value = mock_proc

        result = await self.client.generate_text_async("번역할 원문")
        self.assertEqual(result, "번역된 텍스트 결과입니다.")

        # 명령어 인자 검증
        called_args = mock_exec.call_args[0]
        self.assertIn("-p", called_args)
        self.assertIn("-", called_args)
        self.assertIn("--output-format", called_args)
        self.assertIn("json", called_args)
        self.assertIn("--model", called_args)
        self.assertIn("gemini-3.8-flash-high", called_args)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_rate_limit(self, mock_exec):
        """사용량 제한(Rate Limit) 예외 처리 검증"""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (
            b"",
            b"Error: rate limit exceeded. Please try again later.",
        )
        mock_exec.return_value = mock_proc

        with self.assertRaises(BtgApiRateLimitException):
            await self.client.generate_text_async("번역 요청")

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_auth_error(self, mock_exec):
        """인증 오류 예외 처리 검증"""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (
            b"",
            b"Error: Not authenticated. Please login with agy first.",
        )
        mock_exec.return_value = mock_proc

        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("번역 요청")
        self.assertIn("인증 필요", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_text_async_timeout(self, mock_exec):
        """타임아웃 발생 시 예외 처리 검증"""
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_exec.return_value = mock_proc

        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("번역 요청")
        self.assertIn("시간 초과", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("agy not found"))
    async def test_generate_text_async_file_not_found(self, mock_exec):
        """실행 파일 부재 예외 처리 검증"""
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("번역 요청")
        self.assertIn("실행 파일을 찾을 수 없습니다", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_list_models_async_parsed(self, mock_exec):
        """`agy models` 파싱 검증"""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        raw_output = (
            "Fetching available models...\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
        )
        mock_proc.communicate.return_value = (raw_output.encode("utf-8"), b"")
        mock_exec.return_value = mock_proc

        with patch("shutil.which", return_value="C:\\test\\agy.exe"):
            models = await self.client.list_models_async()
            self.assertIn("default", models)
            self.assertIn("gemini-3.8-flash-high", models)
            self.assertIn("claude-sonnet-4-6", models)

    @patch.object(AntigravityCliClient, "generate_text_async", new_callable=AsyncMock)
    async def test_check_health_async_success(self, mock_gen):
        """체크 헬스 정상 성공 검증"""
        mock_gen.return_value = "OK"
        with patch("shutil.which", return_value="C:\\test\\agy.exe"):
            ok, msg = await self.client.check_health_async()
            self.assertTrue(ok)
            self.assertIn("인증 성공", msg)


if __name__ == "__main__":
    unittest.main()
