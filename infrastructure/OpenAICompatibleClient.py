# c:\Users\Hyunwoo_Room\Downloads\Utility\Neo_Batch_Translator\infrastructure\OpenAICompatibleClient.py
import os
import re
import json
import copy
import logging
import time
import random
from typing import Dict, Any, Iterable, Optional, Union, List
import threading # RPM Lock을 위해 추가

import requests # Using requests for simplicity, consider httpx for async/advanced features

try:
    # If OpenAICompatibleClient.py is in the same parent package as logger_config.py
    from ..infrastructure.logger_config import setup_logger
except ImportError:
    # Fallback for direct execution or if the above fails
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

# --- Custom Exception Classes ---
class OpenAICompatibleApiException(Exception):
    """Base exception for OpenAICompatibleClient errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_info: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_info = error_info

class OpenAICompatibleAuthException(OpenAICompatibleApiException):
    """Authentication or Authorization error (e.g., 401, 403)."""
    pass

class OpenAICompatibleRateLimitException(OpenAICompatibleApiException):
    """Rate limit exceeded error (e.g., 429)."""
    pass

class OpenAICompatibleInvalidRequestException(OpenAICompatibleApiException):
    """Invalid request error (e.g., 400)."""
    pass

class OpenAICompatibleNotFoundException(OpenAICompatibleApiException):
    """Resource not found error (e.g., 404)."""
    pass

class OpenAICompatibleServerException(OpenAICompatibleApiException):
    """Server-side error (e.g., 500, 503)."""
    pass

import asyncio
from core.exceptions import BtgApiContentSafetyException
from infrastructure.base_client import BaseLLMClient, is_content_safety_error
from infrastructure.reasoning_options import OPENAI_COMPATIBLE as OPENAI_COMPATIBLE_REASONING


class OpenAICompatibleClient(BaseLLMClient):
    _DEFAULT_TIMEOUT_SECONDS = 60 # Default timeout for requests

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def supports_pagefold(self) -> bool:
        return False

    def __init__(self,
                 api_key: str,
                 base_url: str, # Should be the full URL to the chat completions endpoint
                 default_model: Optional[str] = None,
                 requests_per_minute: Optional[float] = None,
                 request_timeout: Optional[int] = None,
                 reasoning_effort: Optional[str] = None):
        if not api_key:
            raise ValueError("API key must be provided.")
        if not base_url:
            raise ValueError("Base URL (chat completions endpoint) must be provided.")

        self.api_key = api_key
        url = base_url.rstrip('/')
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        self.base_url = url
        self.default_model = default_model
        self.reasoning_effort = OPENAI_COMPATIBLE_REASONING.normalize(reasoning_effort)
        self.request_timeout = request_timeout if request_timeout is not None else self._DEFAULT_TIMEOUT_SECONDS

        self.requests_per_minute = requests_per_minute
        self.delay_between_requests = 0.0
        if self.requests_per_minute and self.requests_per_minute > 0:
            self.delay_between_requests = 60.0 / self.requests_per_minute
        self.last_request_timestamp = 0.0
        self._rpm_lock = threading.Lock() if hasattr(threading, 'Lock') else None # RPM lock for thread safety

        logger.info(f"OpenAICompatibleClient initialized. Base URL: {self.base_url}, Default Model: {self.default_model}, RPM: {self.requests_per_minute}")

    def _apply_rpm_delay(self):
        """Applies delay to conform to RPM limits."""
        if self.delay_between_requests > 0 and self._rpm_lock:
            with self._rpm_lock:
                current_time = time.monotonic()
                time_since_last_request = current_time - self.last_request_timestamp
                if time_since_last_request < self.delay_between_requests:
                    sleep_duration = self.delay_between_requests - time_since_last_request
                    if sleep_duration > 0:
                        logger.debug(f"RPM: {self.requests_per_minute}, Sleeping for {sleep_duration:.3f}s.")
                        time.sleep(sleep_duration)
                self.last_request_timestamp = time.monotonic()
        elif self.delay_between_requests > 0: # Single-threaded fallback
            current_time = time.monotonic()
            time_since_last_request = current_time - self.last_request_timestamp
            if time_since_last_request < self.delay_between_requests:
                sleep_duration = self.delay_between_requests - time_since_last_request
                if sleep_duration > 0:
                    logger.debug(f"RPM: {self.requests_per_minute}, Sleeping for {sleep_duration:.3f}s.")
                    time.sleep(sleep_duration)
            self.last_request_timestamp = time.monotonic()


    def _prepare_headers(self) -> Dict[str, str]:
        """Prepares headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json" # For non-streaming
        }

    @classmethod
    def _content_to_text(cls, item: Any) -> str:
        if isinstance(item, str):
            return item
        parts = item.get("parts") if isinstance(item, dict) else getattr(item, "parts", None)
        if parts is None:
            if isinstance(item, dict):
                return str(item.get("content") or item.get("text") or "")
            return str(getattr(item, "text", "") or "")
        texts = []
        for p in parts or []:
            if isinstance(p, str):
                texts.append(p)
            else:
                text = p.get("text") if isinstance(p, dict) else getattr(p, "text", None)
                if text:
                    texts.append(str(text))
        return "".join(texts)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """
        모델 응답에서 JSON 문자열을 추출하고 코드 블록이나 앞뒤 설명을 제거합니다.
        """
        t = text.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
        if fence_match:
            candidate = fence_match.group(1).strip()
            if candidate:
                return candidate

        first_bracket = min(
            (pos for pos in (t.find('['), t.find('{')) if pos != -1),
            default=-1
        )
        if first_bracket != -1:
            last_bracket = max(t.rfind(']'), t.rfind('}'))
            if last_bracket > first_bracket:
                return t[first_bracket : last_bracket + 1].strip()

        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t[3:]
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        return t.strip()

    @staticmethod
    def _inline_json_schema_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
        """Pydantic이 만든 `$defs`/`$ref`를 펼쳐 단일 스키마로 만든다."""
        defs = schema.get("$defs") or schema.get("definitions") or {}

        def _resolve(node: Any) -> Any:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/"):
                    name = ref.rsplit("/", 1)[-1]
                    if name in defs:
                        return _resolve(copy.deepcopy(defs[name]))
                return {k: _resolve(v) for k, v in node.items() if k not in ("$defs", "definitions")}
            if isinstance(node, list):
                return [_resolve(v) for v in node]
            return node

        return _resolve(schema)

    @classmethod
    def _build_json_schema_response_format(cls, response_schema: Any) -> Optional[Dict[str, Any]]:
        """
        Pydantic 모델이나 dict 스키마를 OpenAI 규격의 Structured Outputs response_format으로 변환합니다.
        OpenAI API 제약:
        1. 최상위(root)는 반드시 type: "object" 여야 합니다.
        2. 최상위가 array인 경우 {"type": "object", "properties": {"items": array_schema}, "required": ["items"], "additionalProperties": false} 형태로 감쌉니다.
        """
        if response_schema is None:
            return None
        try:
            if isinstance(response_schema, dict):
                raw_schema = dict(response_schema)
            elif hasattr(response_schema, "model_json_schema"):
                raw_schema = response_schema.model_json_schema()
            else:
                from pydantic import TypeAdapter
                raw_schema = TypeAdapter(response_schema).json_schema()

            inlined = cls._inline_json_schema_refs(raw_schema)
            schema_type = inlined.get("type")

            if schema_type == "array" or isinstance(response_schema, (list, tuple)) or getattr(response_schema, "_name", None) in ("List", "Tuple"):
                final_schema = {
                    "type": "object",
                    "properties": {
                        "items": inlined
                    },
                    "required": ["items"],
                    "additionalProperties": False
                }
            elif schema_type == "object" or "properties" in inlined:
                final_schema = dict(inlined)
                if "additionalProperties" not in final_schema:
                    final_schema["additionalProperties"] = False
            else:
                final_schema = {
                    "type": "object",
                    "properties": {
                        "result": inlined
                    },
                    "required": ["result"],
                    "additionalProperties": False
                }

            schema_name = getattr(response_schema, "__name__", None)
            if (schema_name in (None, "list", "tuple", "List", "Tuple")) and hasattr(response_schema, "__args__") and response_schema.__args__:
                inner = response_schema.__args__[0]
                inner_name = getattr(inner, "__name__", "item")
                schema_name = f"{inner_name}_list"
            elif not schema_name:
                schema_name = "response_schema"
            clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(schema_name))[:64] or "response"

            return {
                "type": "json_schema",
                "json_schema": {
                    "name": clean_name,
                    "strict": True,
                    "schema": final_schema
                }
            }
        except Exception as e:
            logger.warning(f"OpenAI 호환 JSON Schema 변환 실패, json_object 모드로 대체합니다: {e}")
            return {"type": "json_object"}

    @staticmethod
    def _coerce_to_schema(parsed: Any, response_schema: Any) -> Any:
        """GeminiClient의 response.parsed와 같은 형태(Pydantic 객체)로 맞춘다. 실패하면 원본 JSON."""
        if response_schema is None or isinstance(response_schema, dict):
            return parsed
        try:
            from pydantic import TypeAdapter
            adapter = TypeAdapter(response_schema)
            if isinstance(parsed, dict):
                try:
                    return adapter.validate_python(parsed)
                except Exception:
                    pass
                try:
                    return adapter.validate_python([parsed])
                except Exception:
                    pass
                for k in ("items", "translations", "units", "characters", "entities", "terms", "data", "results", "result", "list"):
                    if k in parsed and isinstance(parsed[k], list):
                        try:
                            return adapter.validate_python(parsed[k])
                        except Exception:
                            pass
                for v in parsed.values():
                    if isinstance(v, list):
                        try:
                            return adapter.validate_python(v)
                        except Exception:
                            pass
            return adapter.validate_python(parsed)
        except Exception as e:
            logger.warning(f"OpenAI 호환 응답을 스키마로 검증하지 못해 원본 JSON을 반환합니다: {e}")
            return parsed

    def _prepare_messages(self,
                          prompt: Union[str, Any],
                          system_instruction_text: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Prepares the 'messages' list for the OpenAI API.
        Supports str, list of message dicts, and list of google-genai Content objects.
        """
        messages: List[Dict[str, str]] = []

        if system_instruction_text:
            messages.append({"role": "system", "content": system_instruction_text})

        if isinstance(prompt, str):
            messages.append({"role": "user", "content": prompt})
        elif isinstance(prompt, list):
            for item in prompt:
                if isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                    continue
                role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
                role = str(role or "user").lower()
                if role == "model":
                    role = "assistant"
                elif role not in ("system", "user", "assistant"):
                    role = "user"
                messages.append({"role": role, "content": self._content_to_text(item)})
        elif prompt is not None:
            text = self._content_to_text(prompt) if hasattr(prompt, "parts") else str(prompt)
            messages.append({"role": "user", "content": text})

        if not any(msg["role"] == "user" for msg in messages):
            if system_instruction_text:
                messages.append({"role": "user", "content": "Continue."})
            else:
                raise ValueError("Prompt must contain at least one user message or a system instruction.")

        return messages

    def _handle_api_error(self, response: requests.Response):
        """Handles API errors and raises appropriate custom exceptions."""
        status_code = response.status_code
        try:
            error_data = response.json()
            error_info = error_data.get("error", {})
            message = error_info.get("message", response.text)
        except json.JSONDecodeError:
            error_info = None
            message = response.text

        logger.error(f"API Error: Status {status_code}, Message: {message}, Info: {error_info}")

        if is_content_safety_error(message):
            raise BtgApiContentSafetyException(f"OpenAI 호환 API 콘텐츠 안전 차단: {message}")

        if status_code == 401:
            raise OpenAICompatibleAuthException(f"Authentication failed (401): {message}", status_code, error_info)
        if status_code == 403:
            raise OpenAICompatibleAuthException(f"Permission denied (403): {message}", status_code, error_info)
        elif status_code == 429:
            raise OpenAICompatibleRateLimitException(f"Rate limit exceeded (429): {message}", status_code, error_info)
        elif status_code == 400:
            raise OpenAICompatibleInvalidRequestException(f"Invalid request (400): {message}", status_code, error_info)
        elif status_code == 404:
            raise OpenAICompatibleNotFoundException(f"Not found (404): {message}", status_code, error_info)
        elif status_code >= 500:
            raise OpenAICompatibleServerException(f"Server error ({status_code}): {message}", status_code, error_info)
        else:
            raise OpenAICompatibleApiException(f"API request failed with status {status_code}: {message}", status_code, error_info)

    def generate_text(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        model_name: Optional[str] = None,
        generation_config: Optional[Dict[str, Any]] = None,
        system_instruction_text: Optional[str] = None,
        stream: bool = False,
        max_retries: int = 3,
        initial_backoff: float = 1.0, # seconds
        max_backoff: float = 30.0 # seconds
    ) -> Union[str, Dict[str, Any], Iterable[str]]:
        """
        Generates text using an OpenAI-compatible API.

        Args:
            prompt: The prompt string or a list of message dictionaries.
            model_name: The model to use. Overrides the client's default_model.
            generation_config: Dictionary of generation parameters (e.g., temperature, max_tokens).
                               If 'max_tokens' is not provided, a default might be used by the API.
                               LBI's `useMaxOutputTokensInstead` flag implies 'max_tokens' might be
                               named 'max_output_tokens' by some compatible APIs. The caller should
                               ensure the correct key is used in `generation_config`.
            system_instruction_text: Optional system instruction.
            stream: Whether to stream the response.
            max_retries: Maximum number of retries for transient errors.
            initial_backoff: Initial delay for retries.
            max_backoff: Maximum delay for retries.

        Returns:
            If stream is False: The generated text (str) or a dictionary if the response is JSON.
            If stream is True: An iterable of response chunks (str).
        """
        current_model = model_name or self.default_model
        if not current_model:
            raise ValueError("Model name must be provided either during client initialization or in the method call.")

        messages = self._prepare_messages(prompt, system_instruction_text)
        
        payload = {
            "model": current_model,
            "messages": messages,
            "stream": stream,
        }
        if generation_config:
            payload.update(generation_config)
        if "reasoning" not in payload and "reasoning_effort" not in payload and self.reasoning_effort:
            if "openrouter.ai" in self.base_url:
                payload["reasoning"] = {"effort": self.reasoning_effort}
            else:
                payload["reasoning_effort"] = self.reasoning_effort
        if "plugins" not in payload and "openrouter.ai" in self.base_url:
            payload["plugins"] = [{"id": "response-healing"}]

        headers = self._prepare_headers()
        if stream:
            headers["Accept"] = "text/event-stream"


        logger.debug(f"Request payload: {json.dumps(payload, indent=2)}")

        current_retry = 0
        current_backoff = initial_backoff

        while current_retry <= max_retries:
            try:
                self._apply_rpm_delay()
                logger.info(f"Sending request to {self.base_url} with model {current_model} (Attempt {current_retry + 1})")
                
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    stream=stream,
                    timeout=self.request_timeout
                )

                if response.status_code != 200:
                    # 호환 API 프로바이더가 json_schema 형식을 지원하지 않아 400을 반환한 경우, 1회 json_object 모드로 대체 재시도
                    if (
                        response.status_code == 400
                        and isinstance(payload.get("response_format"), dict)
                        and payload["response_format"].get("type") == "json_schema"
                    ):
                        logger.warning(
                            f"호환 API 프로바이더가 json_schema 형식을 거부했습니다 ({response.text[:200]}). json_object 모드로 대체 재시도합니다."
                        )
                        payload["response_format"] = {"type": "json_object"}
                        continue
                    self._handle_api_error(response) # This will raise an exception

                # Successful response
                if stream:
                    logger.info("Streaming response started.")
                    return self._handle_stream_response(response)
                else:
                    logger.info("Non-streaming response received.")
                    response_data = response.json()
                    logger.debug(f"API Response Data: {json.dumps(response_data, indent=2)}")
                    
                    # Standard OpenAI format
                    if response_data.get("choices") and isinstance(response_data["choices"], list) and len(response_data["choices"]) > 0:
                        choice = response_data["choices"][0]
                        if choice.get("finish_reason") == "content_filter":
                            raise BtgApiContentSafetyException("OpenAI 호환 API 콘텐츠 안전 차단: finish_reason='content_filter'")
                        message_content = choice.get("message", {}).get("content")
                        if message_content is not None:
                            return message_content
                        # Handle function/tool calls if necessary in the future
                        elif response_data["choices"][0].get("message", {}).get("tool_calls"):
                             logger.warning("Received tool_calls in response, returning full message object.")
                             return response_data["choices"][0]["message"]

                    # Fallback for potentially different compatible API structures
                    logger.warning("Response format does not match standard OpenAI chat completion. Returning full JSON.")
                    return response_data

            except requests.exceptions.Timeout:
                logger.warning(f"Request timed out after {self.request_timeout}s.")
                # Treat timeout as a potentially transient error for retries
            except requests.exceptions.RequestException as e:
                logger.warning(f"Network error during API request: {e}")
                # Treat other request exceptions as potentially transient for retries
            except (OpenAICompatibleRateLimitException, OpenAICompatibleServerException) as e:
                logger.warning(f"Retriable API error: {e}")
                # These are explicitly retriable
            
            # If we are here, an error occurred that might be retriable
            if current_retry == max_retries:
                logger.error(f"Max retries ({max_retries}) reached. Failing request.")
                raise # Re-raise the last caught exception or a generic one if none was caught

            logger.info(f"Retrying in {current_backoff:.2f} seconds...")
            time.sleep(current_backoff + random.uniform(0, 0.1 * current_backoff)) # Add jitter
            current_backoff = min(current_backoff * 2, max_backoff)
            current_retry += 1
        
        # Should not be reached if max_retries > 0
        raise OpenAICompatibleApiException("Failed to generate text after multiple retries.")


    def _handle_stream_response(self, response: requests.Response) -> Iterable[str]:
        """Handles streaming responses from the API."""
        try:
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        json_data_str = decoded_line[len("data: "):]
                        if json_data_str.strip() == "[DONE]":
                            logger.info("Stream finished with [DONE] marker.")
                            break
                        try:
                            data = json.loads(json_data_str)
                            # Standard OpenAI streaming format
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content_chunk = delta.get("content")
                            if content_chunk:
                                yield content_chunk
                            # Handle finish_reason if needed, e.g. data.get("choices", [{}])[0].get("finish_reason")
                        except json.JSONDecodeError:
                            logger.warning(f"Could not decode JSON from stream: {json_data_str}")
                            continue # Or yield the raw line if that's desired for some compatible APIs
        except requests.exceptions.ChunkedEncodingError as e:
            logger.error(f"Error while streaming response: {e}")
            # This can happen if the server closes the connection prematurely
            # or if there's a network issue during streaming.
            # Depending on the desired behavior, you might want to raise an exception
            # or try to indicate that the stream was interrupted.
            raise OpenAICompatibleApiException(f"Stream interrupted: {e}") from e
        finally:
            response.close() # Ensure the connection is closed
            logger.info("Streaming response finished or closed.")

    async def generate_text_async(
        self,
        prompt: Union[str, Any],
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        response_schema: Optional[Any] = None,
        multimodal_parts: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> str:
        """
        비동기적으로 텍스트 생성을 수행합니다.
        """
        gen_config_dict: Dict[str, Any] = dict(kwargs.get("generation_config_dict") or {})
        system_text = system_instruction or kwargs.get("system_instruction_text")

        if temperature is None and "temperature" in gen_config_dict:
            temperature = gen_config_dict["temperature"]
        if top_p is None and "top_p" in gen_config_dict:
            top_p = gen_config_dict["top_p"]
        if response_schema is None:
            response_schema = gen_config_dict.get("response_schema")

        wants_json = response_schema is not None or gen_config_dict.get("response_mime_type") == "application/json"

        gen_config: Dict[str, Any] = {}
        if temperature is not None:
            gen_config["temperature"] = temperature
        # OpenAI API Structured Outputs:
        # 1. gen_config_dict에 명시적으로 response_format이 지정된 경우 최우선 존중
        # 2. response_schema가 제공되면 OpenAI 규격의 {"type": "json_schema", "json_schema": ...}로 자동 변환
        #    (루트가 list/array인 경우 OpenAI 제약에 맞춰 {"type": "object", "properties": {"items": ...}}로 자동 래핑)
        # 3. response_schema가 없고 wants_json만 켜진 경우(예: 무결성 번역):
        #    배열 출력을 요구하는 프롬프트와의 충돌을 막기 위해 response_format을 강제하지 않음
        if "response_format" in gen_config_dict:
            gen_config["response_format"] = gen_config_dict["response_format"]
        elif response_schema is not None:
            gen_config["response_format"] = self._build_json_schema_response_format(response_schema)
        if self.reasoning_effort:
            if "openrouter.ai" in self.base_url:
                gen_config["reasoning"] = {"effort": self.reasoning_effort}
            else:
                gen_config["reasoning_effort"] = self.reasoning_effort

        # 모델 선택: 도메인 서비스가 전달하는 gemini-* 모델명은 무시하고 클라이언트에 지정된 default_model 사용
        req_model = kwargs.get("model_name")
        if self.default_model:
            if not req_model or req_model.startswith("gemini-") or req_model.startswith("models/"):
                model = self.default_model
            else:
                model = req_model
        else:
            model = req_model

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None,
            lambda: self.generate_text(
                prompt=prompt,
                model_name=model,
                generation_config=gen_config,
                system_instruction_text=system_text,
                stream=False,
            ),
        )

        if not wants_json:
            if isinstance(res, dict):
                return json.dumps(res, ensure_ascii=False)
            return str(res)

        # JSON / 구조화 출력 처리
        if isinstance(res, (dict, list)):
            return self._coerce_to_schema(res, response_schema)

        text_content = str(res)
        try:
            parsed = json.loads(self._strip_code_fence(text_content), strict=False)
            return self._coerce_to_schema(parsed, response_schema)
        except (json.JSONDecodeError, ValueError) as e:
            snippet = ""
            if isinstance(e, json.JSONDecodeError):
                clean_text = self._strip_code_fence(text_content)
                pos = getattr(e, "pos", 0)
                start = max(0, pos - 40)
                end = min(len(clean_text), pos + 40)
                snippet = f" | 오류 부근 스니펫: ...{clean_text[start:end]!r}..."
            schema_name = getattr(response_schema, "__name__", str(response_schema)) if response_schema else "array/raw"
            logger.warning(f"OpenAI 호환 API JSON 응답 파싱 실패 (스키마: {schema_name}), 원문 텍스트를 반환합니다: {e}{snippet}")
            return text_content

    async def list_models_async(self) -> List[str]:
        """사용 가능한 기본 모델 목록 반환"""
        if self.default_model:
            return ["default", self.default_model]
        return ["default", "gpt-4o", "gpt-4o-mini", "deepseek-chat", "deepseek-reasoner"]

    async def check_health_async(self) -> tuple[bool, str]:
        """
        OpenAI 호환 API 엔드포인트 연결 상태를 점검합니다.
        """
        if not self.base_url:
            return False, "API Base URL이 지정되지 않았습니다."
        try:
            test_prompt = "Say 'OK' in one word."
            response = await asyncio.wait_for(
                self.generate_text_async(test_prompt),
                timeout=15,
            )
            clean_resp = str(response).replace("\n", " ").strip()
            return True, f"OpenAI 호환 API 연결 성공 (응답: {clean_resp[:30]})"
        except Exception as e:
            return False, f"OpenAI 호환 API 연결 실패: {e}"


if __name__ == '__main__':
    # --- Configuration for testing ---
    # Replace with your actual API key and a test OpenAI-compatible endpoint
    # For example, using a local Ollama server:
    # TEST_API_KEY = "ollama" # Ollama doesn't strictly need a key if not configured
    # TEST_BASE_URL = "http://localhost:11434/api/chat" # Note: Ollama's /api/chat is slightly different
    # TEST_MODEL = "llama3"

    # Or a mock server like https://mock.Tldraw.com/openai/v1/chat/completions
    TEST_API_KEY = os.environ.get("OPENAI_API_KEY", "YOUR_API_KEY") # Fallback
    TEST_BASE_URL = "https://api.openai.com/v1/chat/completions" # Standard OpenAI
    TEST_MODEL = "gpt-3.5-turbo"

    if TEST_API_KEY == "YOUR_API_KEY" and "OPENAI_API_KEY" not in os.environ:
        print("Please set the OPENAI_API_KEY environment variable or update TEST_API_KEY in the script.")
    else:
        # Basic logging for the test
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger.setLevel(logging.DEBUG) # Set client's logger to DEBUG for more verbose output

        client = OpenAICompatibleClient(
            api_key=TEST_API_KEY,
            base_url=TEST_BASE_URL,
            default_model=TEST_MODEL,
            requests_per_minute=10 # Example RPM
        )

        # --- Test 1: Non-streaming ---
        print("\n--- Test 1: Non-streaming ---")
        try:
            prompt_text = "Tell me a short joke."
            system_instruction = "You are a helpful assistant that tells jokes."
            generation_params = {
                "temperature": 0.7,
                "max_tokens": 50
            }
            response_content = client.generate_text(
                prompt=prompt_text,
                system_instruction_text=system_instruction,
                generation_config=generation_params,
                stream=False
            )
            print(f"Non-streaming response: {response_content}")
        except OpenAICompatibleApiException as e:
            print(f"Error in non-streaming test: {e}")
        except Exception as e:
            print(f"Unexpected error in non-streaming test: {e}")

        # --- Test 2: Streaming ---
        print("\n--- Test 2: Streaming ---")
        try:
            prompt_messages = [
                {"role": "user", "content": "What is the capital of France?"}
            ]
            generation_params_stream = {
                "temperature": 0.5,
                "max_tokens": 100
            }
            print("Streaming response:")
            full_streamed_response = []
            for chunk in client.generate_text(
                prompt=prompt_messages,
                generation_config=generation_params_stream,
                stream=True
            ):
                print(chunk, end='', flush=True)
                full_streamed_response.append(chunk)
            print("\n--- End of stream ---")
            logger.info(f"Full streamed response assembled: {''.join(full_streamed_response)}")
        except OpenAICompatibleApiException as e:
            print(f"\nError in streaming test: {e}")
        except Exception as e:
            print(f"\nUnexpected error in streaming test: {e}")

        # --- Test 3: Error Handling (e.g., invalid model if API supports it) ---
        # This test might vary depending on the specific compatible API
        print("\n--- Test 3: Invalid Model (example error) ---")
        try:
            client.generate_text(prompt="Hello", model_name="invalid-model-name-hopefully")
        except OpenAICompatibleInvalidRequestException as e:
            print(f"Caught expected invalid request error: {e.message} (Status: {e.status_code})")
        except OpenAICompatibleNotFoundException as e:
             print(f"Caught expected not found error: {e.message} (Status: {e.status_code})")
        except OpenAICompatibleApiException as e:
            print(f"Caught API error: {e.message} (Status: {e.status_code})")
        except Exception as e:
            print(f"Unexpected error in error handling test: {e}")
