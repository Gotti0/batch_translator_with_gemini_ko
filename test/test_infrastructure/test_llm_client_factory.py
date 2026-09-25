"""
LLM Client Factory Unit Tests
"""

import unittest
from unittest.mock import patch, MagicMock

from infrastructure.llm_client_factory import LLMClientFactory
from infrastructure.gemini_client import GeminiClient
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
