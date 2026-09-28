"""Unit tests for OpenAICompatibleClient."""

import unittest
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel

from infrastructure.OpenAICompatibleClient import OpenAICompatibleClient
from domain.glossary_service import ApiGlossaryTerm


class TestOpenAICompatibleClient(unittest.IsolatedAsyncioTestCase):
    def test_base_url_normalization(self):
        """base_url에 /chat/completions가 없으면 자동으로 덧붙인다."""
        c1 = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="test-model"
        )
        self.assertEqual(c1.base_url, "https://openrouter.ai/api/v1/chat/completions")

        c2 = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            default_model="test-model"
        )
        self.assertEqual(c2.base_url, "https://openrouter.ai/api/v1/chat/completions")

        c3 = OpenAICompatibleClient(
            api_key="key",
            base_url="http://localhost:1234/v1/",
            default_model="test-model"
        )
        self.assertEqual(c3.base_url, "http://localhost:1234/v1/chat/completions")

    def test_prepare_messages_supports_str_and_content_objects(self):
        """문자열뿐 아니라 Content 객체와 dict 목록도 messages 형식으로 정규화한다."""
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://api.openai.com/v1",
            default_model="test-model"
        )

        # 1. 일반 문자열
        msgs1 = client._prepare_messages("Hello", system_instruction_text="You are helpful.")
        self.assertEqual(len(msgs1), 2)
        self.assertEqual(msgs1[0], {"role": "system", "content": "You are helpful."})
        self.assertEqual(msgs1[1], {"role": "user", "content": "Hello"})

        # 2. Content 형태 (role='model', parts=[...])
        class MockPart:
            def __init__(self, text):
                self.text = text

        class MockContent:
            def __init__(self, role, parts):
                self.role = role
                self.parts = parts

        content_list = [
            MockContent(role="user", parts=[MockPart("Q1")]),
            MockContent(role="model", parts=[MockPart("A1")]),
            MockContent(role="user", parts=[MockPart("Q2")]),
        ]
        msgs2 = client._prepare_messages(content_list)
        self.assertEqual(len(msgs2), 3)
        self.assertEqual(msgs2[0], {"role": "user", "content": "Q1"})
        self.assertEqual(msgs2[1], {"role": "assistant", "content": "A1"})
        self.assertEqual(msgs2[2], {"role": "user", "content": "Q2"})

    def test_strip_code_fence(self):
        """마크다운 코드 블록이나 텍스트 내 JSON 구간을 올바르게 발라낸다."""
        raw = "Here is the response:\n```json\n[{\"keyword\": \"test\"}]\n```\nHope it helps!"
        stripped = OpenAICompatibleClient._strip_code_fence(raw)
        self.assertEqual(stripped, '[{"keyword": "test"}]')

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_model_name_precedence(self, mock_gen):
        """도메인 서비스가 넘긴 gemini-* 모델명은 무시하고 default_model을 사용한다."""
        mock_gen.return_value = "OK"
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        # 도메인 서비스가 config의 model_name('gemini-3.8-flash')을 넘겼을 때
        await client.generate_text_async("prompt", model_name="gemini-3.8-flash")
        call_kwargs = mock_gen.call_args.kwargs
        self.assertEqual(call_kwargs["model_name"], "z-ai/glm-5.3-flash")

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_structured_output_coercion(self, mock_gen):
        """response_schema가 지정되면 마크다운 코드 블록을 벗겨 Pydantic 객체로 변환해 반환한다."""
        mock_gen.return_value = """```json
[
  {"keyword": "惠蓉", "translated_keyword": "혜용", "target_language": "ko", "occurrence_count": 33}
]
```"""
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        res = await client.generate_text_async(
            prompt="추출해줘",
            generation_config_dict={
                "response_mime_type": "application/json",
                "response_schema": list[ApiGlossaryTerm],
            }
        )

        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 1)
        self.assertIsInstance(res[0], ApiGlossaryTerm)
        self.assertEqual(res[0].keyword, "惠蓉")
        self.assertEqual(res[0].translated_keyword, "혜용")
        self.assertEqual(res[0].occurrence_count, 33)


if __name__ == "__main__":
    unittest.main()
