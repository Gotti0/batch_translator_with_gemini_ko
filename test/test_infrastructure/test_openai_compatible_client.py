"""Unit tests for OpenAICompatibleClient."""

import unittest
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel

from infrastructure.OpenAICompatibleClient import OpenAICompatibleClient
from domain.glossary_service import ApiGlossaryTerm
from core.exceptions import BtgApiContentSafetyException


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

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_unwraps_dictionary_wrapped_list_for_schema(self, mock_gen):
        """모델이 list[T] 스키마에 대해 {'characters': [...]} 같은 딕셔너리로 감싸 반환해도 올바르게 언래핑하여 검증한다."""
        from domain.memory_extractor import ExtractedEntity
        mock_gen.return_value = '{"characters": [{"name": "惠蓉", "translated_name": "혜용", "category": "character", "note": "테스트"}]}'
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        res = await client.generate_text_async(
            prompt="추출해줘",
            generation_config_dict={
                "response_mime_type": "application/json",
                "response_schema": list[ExtractedEntity],
            }
        )

        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 1)
        self.assertIsInstance(res[0], ExtractedEntity)
        self.assertEqual(res[0].name, "惠蓉")
        self.assertEqual(res[0].translated_name, "혜용")

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_unwraps_single_dict_into_list_for_schema(self, mock_gen):
        """모델이 list[T] 스키마에 대해 배열이 아닌 단일 객체 {...}만 반환해도 리스트로 감싸서 정상 검증한다."""
        from domain.memory_extractor import ExtractedEntity
        mock_gen.return_value = '{"name": "冯慧兰", "translated_name": "풍혜란", "category": "character", "note": "반말"}'
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        res = await client.generate_text_async(
            prompt="추출해줘",
            generation_config_dict={
                "response_mime_type": "application/json",
                "response_schema": list[ExtractedEntity],
            }
        )

        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 1)
        self.assertIsInstance(res[0], ExtractedEntity)
        self.assertEqual(res[0].name, "冯慧兰")
        self.assertEqual(res[0].translated_name, "풍혜란")

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_does_not_force_json_object_for_arrays(self, mock_gen):
        """무결성 번역처럼 response_mime_type만 있고 스키마가 없거나 배열인 경우 response_format json_object를 강제하지 않는다."""
        mock_gen.return_value = '[{"id": 0, "translated_text": "테스트"}]'
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        res = await client.generate_text_async(
            prompt="배열로 번역해줘",
            generation_config_dict={"response_mime_type": "application/json", "temperature": 0.3}
        )

        call_config = mock_gen.call_args.kwargs["generation_config"]
        # response_format: {"type": "json_object"}가 페이로드에 들어가지 않아야 모델이 배열([])을 반환할 수 있음
        self.assertNotIn("response_format", call_config)
        self.assertIsInstance(res, list)
        self.assertEqual(res[0]["translated_text"], "테스트")

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_reasoning_effort_openrouter(self, mock_gen):
        """OpenRouter 엔드포인트는 reasoning: {'effort': ...} 구조로 전달한다."""
        mock_gen.return_value = "OK"
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash",
            reasoning_effort="low"
        )

        await client.generate_text_async(prompt="test")
        call_config = mock_gen.call_args.kwargs["generation_config"]
        self.assertEqual(call_config.get("reasoning"), {"effort": "low"})
        self.assertNotIn("reasoning_effort", call_config)

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_reasoning_effort_standard_openai(self, mock_gen):
        """표준 OpenAI 엔드포인트는 최상위 reasoning_effort 문자열로 전달한다."""
        mock_gen.return_value = "OK"
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://api.openai.com/v1",
            default_model="o3-mini",
            reasoning_effort="low"
        )
        await client.generate_text_async(prompt="test")
        call_config = mock_gen.call_args.kwargs["generation_config"]
        self.assertEqual(call_config.get("reasoning_effort"), "low")
        self.assertNotIn("reasoning", call_config)

    @patch.object(OpenAICompatibleClient, "generate_text")
    async def test_generate_text_async_json_decode_error_logs_snippet_and_schema(self, mock_gen):
        """JSON 파싱 실패 시 경고 로그에 스키마 이름과 오류 부근 스니펫(±40자)이 포함된다."""
        mock_gen.return_value = '{"name": "test", broken_json'
        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )

        with self.assertLogs("infrastructure.OpenAICompatibleClient", level="WARNING") as cm:
            res = await client.generate_text_async(
                prompt="test",
                generation_config_dict={"response_mime_type": "application/json"}
            )
            self.assertEqual(res, '{"name": "test", broken_json')
            self.assertTrue(any("오류 부근 스니펫" in msg and "array/raw" in msg for msg in cm.output))

    def test_build_json_schema_response_format_for_single_and_list_models(self):
        """단일 모델은 object로, list[T] 모델은 items로 래핑된 object json_schema로 변환된다."""
        from domain.memory_extractor import ExtractedEntity

        # 1. 단일 모델
        single_rf = OpenAICompatibleClient._build_json_schema_response_format(ExtractedEntity)
        self.assertEqual(single_rf["type"], "json_schema")
        self.assertTrue(single_rf["json_schema"]["strict"])
        self.assertEqual(single_rf["json_schema"]["name"], "ExtractedEntity")
        self.assertEqual(single_rf["json_schema"]["schema"]["type"], "object")
        self.assertFalse(single_rf["json_schema"]["schema"]["additionalProperties"])
        self.assertIn("name", single_rf["json_schema"]["schema"]["properties"])

        # 2. 리스트 모델 (OpenAI root array 제약 회피를 위한 wrapper)
        list_rf = OpenAICompatibleClient._build_json_schema_response_format(list[ExtractedEntity])
        self.assertEqual(list_rf["type"], "json_schema")
        self.assertTrue(list_rf["json_schema"]["strict"])
        self.assertEqual(list_rf["json_schema"]["name"], "ExtractedEntity_list")
        self.assertEqual(list_rf["json_schema"]["schema"]["type"], "object")
        self.assertEqual(list_rf["json_schema"]["schema"]["required"], ["items"])
        self.assertEqual(list_rf["json_schema"]["schema"]["properties"]["items"]["type"], "array")

    @patch("requests.post")
    def test_generate_text_injects_openrouter_response_healing_plugin(self, mock_post):
        """OpenRouter 엔드포인트 호출 시 payload에 response-healing 플러그인이 주입된다."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_post.return_value = mock_resp

        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
            default_model="z-ai/glm-5.3-flash"
        )
        client.generate_text(prompt="hello")

        call_json = mock_post.call_args.kwargs["json"]
        self.assertIn("plugins", call_json)
        self.assertEqual(call_json["plugins"], [{"id": "response-healing"}])

    @patch("requests.post")
    def test_generate_text_falls_back_to_json_object_when_json_schema_returns_400(self, mock_post):
        """호환 프로바이더가 json_schema에 대해 400을 반환하면 1회 json_object 모드로 fallback 재시도한다."""
        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.text = "json_schema not supported"
        err_resp.json.return_value = {"error": {"message": "json_schema not supported"}}

        succ_resp = MagicMock()
        succ_resp.status_code = 200
        succ_resp.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}

        mock_post.side_effect = [err_resp, succ_resp]

        client = OpenAICompatibleClient(
            api_key="key",
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o"
        )
        res = client.generate_text(
            prompt="test",
            generation_config={"response_format": {"type": "json_schema", "json_schema": {}}}
        )
        self.assertEqual(res, '{"ok": true}')
        self.assertEqual(mock_post.call_count, 2)
        second_call_json = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(second_call_json["response_format"], {"type": "json_object"})

    @patch("requests.post")
    def test_generate_text_content_filter_finish_reason(self, mock_post):
        """finish_reason이 content_filter인 경우 BtgApiContentSafetyException을 발생시킨다."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{
                "message": {"content": ""},
                "finish_reason": "content_filter"
            }]
        }
        mock_post.return_value = resp

        client = OpenAICompatibleClient(api_key="key", base_url="https://api.openai.com/v1", default_model="gpt-4o")
        with self.assertRaises(BtgApiContentSafetyException) as ctx:
            client.generate_text("sensitive prompt")
        self.assertIn("content_filter", str(ctx.exception))

    @patch("requests.post")
    def test_handle_api_error_content_safety(self, mock_post):
        """400 또는 403 오류 응답에 안전 정책 위반 내용이 포함된 경우 BtgApiContentSafetyException을 발생시킨다."""
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"error": {"message": "The response was filtered due to the prompt triggering Azure OpenAI safety policy."}}'
        resp.json.return_value = {
            "error": {"message": "The response was filtered due to the prompt triggering Azure OpenAI safety policy."}
        }
        mock_post.return_value = resp

        client = OpenAICompatibleClient(api_key="key", base_url="https://api.openai.com/v1", default_model="gpt-4o")
        with self.assertRaises(BtgApiContentSafetyException) as ctx:
            client.generate_text("sensitive prompt")
        self.assertIn("안전 차단", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
