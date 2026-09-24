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


if __name__ == "__main__":
    unittest.main()
