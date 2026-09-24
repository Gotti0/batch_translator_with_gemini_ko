"""
Unit tests for OllamaClient adapter.
"""

import json
import unittest
from typing import List
from unittest.mock import MagicMock, patch

import requests
from google.genai import types as genai_types
from pydantic import BaseModel

from core.exceptions import (
    BtgApiClientException,
    BtgApiInvalidRequestException,
    BtgApiRateLimitException,
)
from infrastructure.ollama_client import OllamaClient, _inline_json_schema_refs


def _response(status_code=200, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    if payload is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = payload
    resp.text = text or (json.dumps(payload, ensure_ascii=False) if payload is not None else "")
    return resp


def _chat_payload(content, **extra):
    data = {"model": "gemma3:12b", "message": {"role": "assistant", "content": content}, "done": True}
    data.update(extra)
    return data


class _Term(BaseModel):
    keyword: str
    translated_keyword: str


class TestOllamaClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = OllamaClient(base_url="http://localhost:11434", model_name="gemma3:12b", num_ctx=16384)

    def test_initialization_properties(self):
        self.assertEqual(self.client.provider_name, "ollama")
        self.assertFalse(self.client.supports_pagefold)
        self.assertEqual(self.client.model_name, "gemma3:12b")
        self.assertEqual(self.client.num_ctx, 16384)

    def test_base_url_normalization(self):
        """OpenAI 호환 주소나 엔드포인트를 붙여 넣어도 서버 루트로 정규화"""
        cases = {
            "localhost:11434": "http://localhost:11434",
            "http://localhost:11434/": "http://localhost:11434",
            "http://localhost:11434/v1/chat/completions": "http://localhost:11434",
            "http://gpu-box:11434/api/chat": "http://gpu-box:11434",
            "http://gpu-box:11434/v1": "http://gpu-box:11434",
            "": "http://localhost:11434",
        }
        for raw, expected in cases.items():
            self.assertEqual(OllamaClient(base_url=raw, model_name="m").base_url, expected, raw)

    @patch("infrastructure.ollama_client.requests.request")
    async def test_generate_with_gemini_style_kwargs(self, mock_request):
        """도메인 서비스의 GeminiClient 호출 형식(Content 리스트, system_instruction_text 등)을 변환"""
        mock_request.return_value = _response(payload=_chat_payload("번역 결과", prompt_eval_count=100))

        prompt = [
            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="예시 원문")]),
            genai_types.Content(role="model", parts=[genai_types.Part.from_text(text="예시 번역")]),
            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="번역할 원문")]),
        ]
        result = await self.client.generate_text_async(
            prompt=prompt,
            model_name="gemini-2.0-flash",  # Gemini 설정값은 무시되어야 함
            generation_config_dict={"temperature": 0.4, "top_p": 0.8, "thinking_level": "high"},
            thinking_budget=None,
            system_instruction_text="너는 번역가다.",
            stream=False,
            multimodal_parts=None,
        )
        self.assertEqual(result, "번역 결과")

        method, url = mock_request.call_args[0]
        payload = mock_request.call_args[1]["json"]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://localhost:11434/api/chat")
        self.assertEqual(payload["model"], "gemma3:12b")
        self.assertFalse(payload["stream"])
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "너는 번역가다."},
                {"role": "user", "content": "예시 원문"},
                {"role": "assistant", "content": "예시 번역"},
                {"role": "user", "content": "번역할 원문"},
            ],
        )
        self.assertEqual(payload["options"], {"temperature": 0.4, "top_p": 0.8, "num_ctx": 16384})
        self.assertNotIn("format", payload)
        self.assertNotIn("Authorization", mock_request.call_args[1]["headers"])

    @patch("infrastructure.ollama_client.requests.request")
    async def test_think_block_is_stripped(self, mock_request):
        mock_request.return_value = _response(payload=_chat_payload("<think>\n고민 중...\n</think>\n\n최종 번역"))
        result = await self.client.generate_text_async("원문")
        self.assertEqual(result, "최종 번역")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_json_mode_returns_parsed_object(self, mock_request):
        """무결성 모드(response_mime_type=json)는 format=json으로 요청하고 파싱된 객체를 반환"""
        units = [{"id": "1", "translated_text": "안녕"}]
        mock_request.return_value = _response(payload=_chat_payload("```json\n" + json.dumps(units) + "\n```"))

        result = await self.client.generate_text_async(
            prompt="원문",
            generation_config_dict={"response_mime_type": "application/json"},
        )
        self.assertEqual(result, units)
        self.assertEqual(mock_request.call_args[1]["json"]["format"], "json")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_pydantic_schema_is_sent_and_validated(self, mock_request):
        """용어집 추출처럼 Pydantic 스키마를 주면 JSON Schema로 제약하고 모델 객체로 검증"""
        terms = [{"keyword": "勇者", "translated_keyword": "용사"}]
        mock_request.return_value = _response(payload=_chat_payload(json.dumps(terms, ensure_ascii=False)))

        result = await self.client.generate_text_async(
            prompt="원문",
            generation_config_dict={"response_mime_type": "application/json", "response_schema": List[_Term]},
        )
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], _Term)
        self.assertEqual(result[0].translated_keyword, "용사")

        fmt = mock_request.call_args[1]["json"]["format"]
        self.assertEqual(fmt["type"], "array")
        self.assertNotIn("$defs", fmt)
        self.assertEqual(fmt["items"]["properties"]["keyword"]["type"], "string")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_json_with_code_block_and_commentary(self, mock_request):
        raw = "분석 결과입니다:\n```json\n[{\"keyword\": \"勇者\", \"translated_keyword\": \"용사\"}]\n```\n참고 바랍니다."
        mock_request.return_value = _response(payload=_chat_payload(raw))
        result = await self.client.generate_text_async(
            prompt="원문",
            generation_config_dict={"response_mime_type": "application/json", "response_schema": List[_Term]},
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].translated_keyword, "용사")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_invalid_json_falls_back_to_text(self, mock_request):
        mock_request.return_value = _response(payload=_chat_payload("JSON이 아닌 응답"))
        result = await self.client.generate_text_async(
            prompt="원문", generation_config_dict={"response_mime_type": "application/json"}
        )
        self.assertEqual(result, "JSON이 아닌 응답")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_api_key_sent_as_bearer(self, mock_request):
        client = OllamaClient(model_name="gemma3:12b", api_key="secret")
        mock_request.return_value = _response(payload=_chat_payload("ok"))
        await client.generate_text_async("hi")
        self.assertEqual(mock_request.call_args[1]["headers"]["Authorization"], "Bearer secret")

    async def test_missing_model_raises(self):
        client = OllamaClient(model_name="")
        with self.assertRaises(BtgApiInvalidRequestException):
            await client.generate_text_async("hi")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_model_not_found_suggests_pull(self, mock_request):
        mock_request.return_value = _response(404, {"error": "model 'gemma3:12b' not found"})
        with self.assertRaises(BtgApiInvalidRequestException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("ollama pull gemma3:12b", str(ctx.exception))

    @patch("infrastructure.ollama_client.requests.request")
    async def test_server_busy_maps_to_rate_limit(self, mock_request):
        mock_request.return_value = _response(503, {"error": "server busy"})
        with self.assertRaises(BtgApiRateLimitException):
            await self.client.generate_text_async("hi")

    @patch("infrastructure.ollama_client.requests.request")
    async def test_connection_error_message(self, mock_request):
        mock_request.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("ollama serve", str(ctx.exception))

    @patch("infrastructure.ollama_client.requests.request")
    async def test_list_models(self, mock_request):
        mock_request.return_value = _response(
            payload={"models": [{"name": "qwen3:14b"}, {"name": "gemma3:12b"}, {"model": "llama3.1:latest"}]}
        )
        models = await self.client.list_models_async()
        self.assertEqual(models, ["gemma3:12b", "llama3.1:latest", "qwen3:14b"])
        self.assertEqual(mock_request.call_args[0], ("GET", "http://localhost:11434/api/tags"))

    @patch("infrastructure.ollama_client.requests.request")
    async def test_health_check_success_and_missing_model(self, mock_request):
        def fake_request(method, url, **kwargs):
            if url.endswith("/api/version"):
                return _response(payload={"version": "0.12.0"})
            return _response(payload={"models": [{"name": "gemma3:12b"}, {"name": "llama3.1:latest"}]})

        mock_request.side_effect = fake_request

        ok, msg = await self.client.check_health_async()
        self.assertTrue(ok, msg)
        self.assertIn("0.12.0", msg)

        # ':latest' 태그는 생략해도 설치된 것으로 인정
        ok, _ = await OllamaClient(model_name="llama3.1").check_health_async()
        self.assertTrue(ok)

        ok, msg = await OllamaClient(model_name="qwen3:32b").check_health_async()
        self.assertFalse(ok)
        self.assertIn("ollama pull qwen3:32b", msg)

    @patch("infrastructure.ollama_client.requests.request")
    async def test_health_check_server_down(self, mock_request):
        mock_request.side_effect = requests.exceptions.ConnectionError("refused")
        ok, msg = await self.client.check_health_async()
        self.assertFalse(ok)
        self.assertIn("연결할 수 없습니다", msg)

    def test_inline_json_schema_refs(self):
        schema = {
            "type": "array",
            "items": {"$ref": "#/$defs/T"},
            "$defs": {"T": {"type": "object", "properties": {"a": {"type": "string"}}}},
        }
        self.assertEqual(
            _inline_json_schema_refs(schema),
            {"type": "array", "items": {"type": "object", "properties": {"a": {"type": "string"}}}},
        )


if __name__ == "__main__":
    unittest.main()
