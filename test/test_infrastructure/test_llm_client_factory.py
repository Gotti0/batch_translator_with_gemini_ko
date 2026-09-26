"""
LLM Client Factory Unit Tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from infrastructure.llm_client_factory import LLMClientFactory
from google.auth.exceptions import DefaultCredentialsError

from infrastructure.gemini_client import GeminiClient, GeminiInvalidRequestException
from infrastructure.claude_cli_client import ClaudeCliClient
from infrastructure.codex_cli_client import CodexCliClient
from infrastructure.OpenAICompatibleClient import OpenAICompatibleClient


class TestLLMClientFactory(unittest.TestCase):
    @patch("infrastructure.gemini_client.genai.Client")
    def test_create_gemini_client(self, mock_genai):
        cfg = {"llm_provider": "gemini", "api_key": "test-key"}
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, GeminiClient)
        self.assertEqual(client.provider_name, "gemini")
        self.assertTrue(client.supports_pagefold)

    @patch("infrastructure.gemini_client.ServiceAccountCredentials")
    @patch("infrastructure.gemini_client.genai.Client")
    def test_vertex_reads_service_account_file(self, mock_genai, mock_sa_creds):
        """SA 파일 경로가 API 키로 쓰이지 않고 파일 내용으로 Vertex 모드가 된다 (auth_credentials 없는 호출 포함)"""
        sa_info = {"type": "service_account", "project_id": "sa-project"}
        with tempfile.TemporaryDirectory() as tmp:
            sa_path = Path(tmp) / "sa.json"
            sa_path.write_text(json.dumps(sa_info), encoding="utf-8")
            cfg = {"llm_provider": "gemini", "use_vertex_ai": True,
                   "service_account_file_path": str(sa_path), "gcp_location": "global"}
            client = LLMClientFactory.create_client(cfg)

        self.assertEqual(client.auth_mode, "VERTEX_AI")
        self.assertEqual(client.api_keys_list, [])
        self.assertEqual(client.vertex_project, "sa-project")
        self.assertEqual(client.vertex_location, "global")
        self.assertEqual(mock_sa_creds.from_service_account_info.call_args.args[0], sa_info)

    @patch("infrastructure.gemini_client.ServiceAccountCredentials")
    @patch("infrastructure.gemini_client.genai.Client")
    def test_vertex_missing_file_falls_back_to_auth_credentials(self, mock_genai, mock_sa_creds):
        sa_info = {"type": "service_account", "project_id": "fallback-project"}
        cfg = {"llm_provider": "gemini", "use_vertex_ai": True,
               "service_account_file_path": str(Path(tempfile.gettempdir()) / "no-such-sa.json")}
        client = LLMClientFactory.create_client(cfg, auth_credentials=json.dumps(sa_info))

        self.assertEqual(client.auth_mode, "VERTEX_AI")
        self.assertEqual(client.vertex_project, "fallback-project")

    @patch("infrastructure.gemini_client.google.auth.default", return_value=(MagicMock(), "adc-project"))
    @patch("infrastructure.gemini_client.genai.Client")
    def test_vertex_without_service_account_uses_adc_not_api_keys(self, mock_genai, mock_adc):
        """Vertex 토글만 켜면 설정의 API 키로 대체하지 않고 ADC로 Vertex 모드가 된다"""
        cfg = {"llm_provider": "gemini", "use_vertex_ai": True, "api_keys": ["AQ.gemini-key"]}
        client = LLMClientFactory.create_client(cfg)

        self.assertEqual(client.auth_mode, "VERTEX_AI")
        self.assertEqual(client.api_keys_list, [])
        self.assertEqual(client.vertex_project, "adc-project")
        self.assertTrue(mock_genai.call_args.kwargs["vertexai"])
        self.assertNotIn("api_key", mock_genai.call_args.kwargs)

    @patch("infrastructure.gemini_client.genai.Client")
    def test_vertex_unusable_service_account_file_raises(self, mock_genai):
        """SA 경로를 지정했는데 쓸 수 없으면 API 키나 ADC로 넘어가지 않고 오류를 낸다"""
        with tempfile.TemporaryDirectory() as tmp:
            not_sa = Path(tmp) / "not-sa.json"
            not_sa.write_text('{"type": "authorized_user"}', encoding="utf-8")
            for path in (str(Path(tmp) / "missing.json"), str(not_sa)):
                cfg = {"llm_provider": "gemini", "use_vertex_ai": True,
                       "service_account_file_path": path, "api_keys": ["AQ.gemini-key"]}
                with self.assertRaises(GeminiInvalidRequestException, msg=path):
                    LLMClientFactory.create_client(cfg)
        mock_genai.assert_not_called()

    @patch("infrastructure.gemini_client.google.auth.default", side_effect=DefaultCredentialsError("no adc"))
    @patch("infrastructure.gemini_client.genai.Client")
    def test_vertex_without_adc_raises(self, mock_genai, mock_adc):
        with self.assertRaises(GeminiInvalidRequestException):
            LLMClientFactory.create_client({"llm_provider": "gemini", "use_vertex_ai": True,
                                            "api_keys": ["AQ.gemini-key"]})
        mock_genai.assert_not_called()

    def test_create_claude_cli_client(self):
        cfg = {"llm_provider": "claude_cli", "claude_cli_model": "claude-3-5-sonnet-20241022"}
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, ClaudeCliClient)
        self.assertEqual(client.provider_name, "claude_cli")
        self.assertFalse(client.supports_pagefold)

    def test_create_codex_cli_client(self):
        cfg = {"llm_provider": "codex_cli", "codex_cli_model": "gpt-5.5"}
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, CodexCliClient)
        self.assertEqual(client.provider_name, "codex_cli")
        self.assertFalse(client.supports_pagefold)

    def test_cli_clients_do_not_borrow_gemini_keys(self):
        """CLI 전용 키가 없으면 Gemini api_keys를 쓰지 않고 로그인 세션(None)을 쓴다"""
        for provider in ("claude_cli", "codex_cli"):
            client = LLMClientFactory.create_client({"llm_provider": provider, "api_keys": ["AQ.gemini-key"]})
            self.assertIsNone(client.api_key, provider)

        client = LLMClientFactory.create_client({
            "llm_provider": "claude_cli", "api_keys": ["AQ.gemini-key"], "claude_cli_api_key": "sk-ant-own",
        })
        self.assertEqual(client.api_key, "sk-ant-own")

    def test_create_openai_compatible_client(self):
        cfg = {
            "llm_provider": "openai_compatible",
            "openai_compatible_base_url": "https://api.openai.com/v1/chat/completions",
            "openai_compatible_api_key": "test-key",
            "openai_compatible_model": "gpt-4o"
        }
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.provider_name, "openai_compatible")
        self.assertFalse(client.supports_pagefold)

    def test_create_antigravity_cli_client(self):
        from infrastructure.antigravity_cli_client import AntigravityCliClient
        cfg = {"llm_provider": "antigravity_cli", "antigravity_cli_model": "gemini-3.8-flash-high"}
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, AntigravityCliClient)
        self.assertEqual(client.provider_name, "antigravity_cli")
        self.assertFalse(client.supports_pagefold)

    def test_create_ollama_client(self):
        from infrastructure.ollama_client import OllamaClient
        cfg = {
            "llm_provider": "ollama",
            "ollama_base_url": "http://gpu-box:11434/v1",
            "ollama_model": "qwen3:14b",
            "ollama_num_ctx": 32768,
        }
        client = LLMClientFactory.create_client(cfg)
        self.assertIsInstance(client, OllamaClient)
        self.assertEqual(client.provider_name, "ollama")
        self.assertFalse(client.supports_pagefold)
        self.assertEqual(client.base_url, "http://gpu-box:11434")
        self.assertEqual(client.model_name, "qwen3:14b")
        self.assertEqual(client.num_ctx, 32768)


if __name__ == "__main__":
    unittest.main()
