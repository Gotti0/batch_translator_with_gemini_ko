"""
Ollama Client Adapter for Neo Batch Translator (BTG)

로컬(또는 원격) Ollama 서버의 네이티브 REST API(`/api/chat`, `/api/tags`)를 호출하여
API 과금 없이 로컬 GPU의 오픈 모델로 번역을 수행하는 어댑터입니다.

OpenAI 호환 엔드포인트(`/v1/chat/completions`) 대신 네이티브 API를 쓰는 이유:
- `options.num_ctx`로 컨텍스트 길이를 지정할 수 있다. 지정하지 않으면 Ollama 기본값
  (모델/버전에 따라 2K~4K)에서 프롬프트 앞부분이 경고 없이 잘려 번역이 누락된다.
- `format`에 JSON Schema를 넘겨 무결성 모드·용어집 추출의 구조화 출력을 강제할 수 있다.
- `/api/tags`로 실제 설치된 모델 목록을 조회할 수 있다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from core.exceptions import (
    BtgApiClientException,
    BtgApiInvalidRequestException,
    BtgApiRateLimitException,
)
from infrastructure.base_client import BaseLLMClient
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _inline_json_schema_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Pydantic이 만든 `$defs`/`$ref`를 펼쳐 단일 스키마로 만든다.

    Ollama는 JSON Schema를 문법(grammar)으로 변환해 출력을 제약하는데, 참조가 남아 있으면
    버전에 따라 제약이 느슨해지거나 무시될 수 있어 미리 펼쳐 둔다.
    """
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


class OllamaClient(BaseLLMClient):
    """
    Ollama 네이티브 REST API 클라이언트.
    """

    RECOMMENDED_MODELS = [
        "gemma3:12b",
        "gemma3:27b",
        "qwen3:14b",
        "qwen3:32b",
        "exaone3.5:7.8b",
        "exaone3.5:32b",
        "llama3.1:8b",
    ]

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        num_ctx: Optional[int] = 16384,
        keep_alive: Optional[str] = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        """
        OllamaClient를 초기화합니다.

        Args:
            base_url: Ollama 서버 주소 (예: http://localhost:11434). `/api/...`나 `/v1` 접미사는 제거된다.
            model_name: 사용할 모델 태그 (예: gemma3:12b)
            api_key: 선택 사항. 인증 프록시나 Ollama 클라우드를 쓸 때 Bearer 토큰으로 전송
            num_ctx: 컨텍스트 길이(토큰). None 또는 0이면 서버/모델 기본값 사용
            keep_alive: 요청 후 모델을 메모리에 유지할 시간 (예: "10m", "-1"). None이면 서버 기본값
            timeout_seconds: 요청 타임아웃(초). 로컬 추론은 느리므로 넉넉하게 잡는다.
        """
        self.base_url = self._normalize_base_url(base_url)
        self.model_name = model_name.strip() if model_name and model_name.strip() not in ("", "default") else None
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.num_ctx = int(num_ctx) if num_ctx else None
        self.keep_alive = keep_alive or None
        self.timeout_seconds = float(timeout_seconds)

        logger.info(
            f"OllamaClient 초기화 완료 (서버: {self.base_url}, 모델: {self.model_name or '미지정'}, num_ctx: {self.num_ctx or '서버 기본값'})"
        )

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def supports_pagefold(self) -> bool:
        return False

    @staticmethod
    def _normalize_base_url(base_url: Optional[str]) -> str:
        url = (base_url or "").strip() or DEFAULT_OLLAMA_BASE_URL
        if not re.match(r"^https?://", url):
            url = f"http://{url}"
        url = url.rstrip("/")
        # OpenAI 호환 주소나 개별 엔드포인트를 붙여 넣은 경우 서버 루트로 되돌린다.
        for suffix in ("/api/chat", "/api/generate", "/api/tags", "/v1/chat/completions", "/api", "/v1"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        return url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ------------------------------------------------------------------
    # 요청 조립
    # ------------------------------------------------------------------

    @staticmethod
    def _content_to_text(item: Any) -> str:
        parts = getattr(item, "parts", None)
        if parts is None and isinstance(item, dict):
            parts = item.get("parts")
            if parts is None:
                return str(item.get("content") or item.get("text") or "")
        if parts is None:
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

    @classmethod
    def _build_messages(cls, prompt: Union[str, Any], system_instruction: Optional[str]) -> List[Dict[str, str]]:
        """문자열, dict 리스트, google-genai `Content` 리스트를 Ollama messages로 변환한다."""
        messages: List[Dict[str, str]] = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})

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
                messages.append({"role": role, "content": cls._content_to_text(item)})
        elif prompt is not None:
            text = cls._content_to_text(prompt) if hasattr(prompt, "parts") else str(prompt)
            messages.append({"role": "user", "content": text})

        if not any(m["role"] == "user" for m in messages):
            raise BtgApiInvalidRequestException("Ollama 요청에 사용자 메시지가 없습니다.")
        return messages

    @staticmethod
    def _schema_to_format(response_schema: Any) -> Optional[Dict[str, Any]]:
        """Pydantic 모델/제네릭 타입/dict 스키마를 Ollama `format`용 JSON Schema로 변환한다."""
        if response_schema is None:
            return None
        try:
            if isinstance(response_schema, dict):
                schema = response_schema
            elif hasattr(response_schema, "model_json_schema"):
                schema = response_schema.model_json_schema()
            else:
                from pydantic import TypeAdapter

                schema = TypeAdapter(response_schema).json_schema()
            return _inline_json_schema_refs(schema)
        except Exception as e:
            logger.warning(f"Ollama JSON Schema 변환 실패, 일반 JSON 모드로 대체합니다: {e}")
            return None

    @staticmethod
    def _coerce_to_schema(parsed: Any, response_schema: Any) -> Any:
        """GeminiClient의 `response.parsed`와 같은 형태(Pydantic 객체)로 맞춘다. 실패하면 원본 JSON."""
        if response_schema is None or isinstance(response_schema, dict):
            return parsed
        try:
            from pydantic import TypeAdapter

            return TypeAdapter(response_schema).validate_python(parsed)
        except Exception as e:
            logger.warning(f"Ollama 응답을 스키마로 검증하지 못해 원본 JSON을 반환합니다: {e}")
            return parsed

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t[3:]
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        return t.strip()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _raise_for_status(self, response: requests.Response) -> None:
        if response.status_code == 200:
            return
        try:
            message = response.json().get("error") or response.text
        except ValueError:
            message = response.text
        status = response.status_code
        if status == 404 and "not found" in str(message).lower():
            raise BtgApiInvalidRequestException(
                f"Ollama에 모델 '{self.model_name}'이(가) 없습니다. 터미널에서 `ollama pull {self.model_name}`을 실행하세요. ({message})"
            )
        if status in (429, 503):
            raise BtgApiRateLimitException(f"Ollama 서버가 요청을 처리할 수 없습니다 ({status}): {message}")
        if status in (401, 403):
            raise BtgApiClientException(f"Ollama 인증 실패 ({status}): API 키를 확인하세요. {message}")
        if status == 400:
            raise BtgApiInvalidRequestException(f"Ollama 잘못된 요청 (400): {message}")
        raise BtgApiClientException(f"Ollama 요청 실패 ({status}): {message}")

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                json=payload,
                timeout=timeout or self.timeout_seconds,
            )
        except requests.exceptions.ConnectionError as e:
            raise BtgApiClientException(
                f"Ollama 서버({self.base_url})에 연결할 수 없습니다. Ollama가 실행 중인지 확인하세요 (`ollama serve`). ({e})"
            ) from e
        except requests.exceptions.Timeout as e:
            raise BtgApiClientException(
                f"Ollama 응답 시간 초과 ({timeout or self.timeout_seconds:.0f}초). 모델이 너무 크거나 청크가 너무 길 수 있습니다."
            ) from e
        except requests.exceptions.RequestException as e:
            raise BtgApiClientException(f"Ollama 요청 중 네트워크 오류: {e}") from e

        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as e:
            raise BtgApiClientException(f"Ollama 응답을 JSON으로 해석할 수 없습니다: {response.text[:200]}") from e

    async def _request_async(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._request(method, path, payload, timeout))

    # ------------------------------------------------------------------
    # BaseLLMClient 구현
    # ------------------------------------------------------------------

    async def generate_text_async(
        self,
        prompt: Union[str, Any],
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        response_schema: Optional[Any] = None,
        multimodal_parts: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """
        `/api/chat`을 비스트리밍으로 호출합니다.

        도메인 서비스는 GeminiClient 시그니처(`system_instruction_text`, `generation_config_dict`)로
        호출하므로 두 형식을 모두 받는다. `model_name` kwarg는 Gemini용 설정값이므로 무시하고
        클라이언트에 지정된 Ollama 모델을 사용한다.

        Returns:
            일반 모드: 응답 텍스트(str)
            JSON 모드(`response_mime_type == application/json` 또는 스키마 지정): 파싱된 객체.
            스키마가 Pydantic 타입이면 해당 타입으로 검증된 객체를 반환한다 (GeminiClient와 동일).
        """
        if not self.model_name:
            raise BtgApiInvalidRequestException(
                "Ollama 모델이 지정되지 않았습니다. 설정 탭에서 모델을 선택하거나 입력하세요."
            )
        if multimodal_parts:
            logger.debug("Ollama는 PageFold PDF Part를 지원하지 않아 무시합니다.")

        gen_config: Dict[str, Any] = dict(kwargs.get("generation_config_dict") or {})
        system_text = system_instruction or kwargs.get("system_instruction_text")
        if response_schema is None:
            response_schema = gen_config.get("response_schema")
        wants_json = response_schema is not None or gen_config.get("response_mime_type") == "application/json"

        options: Dict[str, Any] = {}
        temp = temperature if temperature is not None else gen_config.get("temperature")
        if temp is not None:
            options["temperature"] = float(temp)
        tp = top_p if top_p is not None else gen_config.get("top_p")
        if tp is not None:
            options["top_p"] = float(tp)
        if gen_config.get("top_k") is not None:
            options["top_k"] = int(gen_config["top_k"])
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(prompt, system_text),
            "stream": False,
        }
        if options:
            payload["options"] = options
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if wants_json:
            payload["format"] = self._schema_to_format(response_schema) or "json"

        data = await self._request_async("POST", "/api/chat", payload)

        message = data.get("message") or {}
        content = str(message.get("content") or "")
        # 추론 모델(qwen3, deepseek-r1 등)이 본문 앞에 붙이는 사고 과정을 제거한다.
        content = _THINK_BLOCK_RE.sub("", content).strip()

        if data.get("done_reason") == "length":
            logger.warning("Ollama 응답이 길이 제한으로 잘렸습니다 (done_reason=length). num_ctx 또는 청크 크기를 조정하세요.")

        prompt_tokens = data.get("prompt_eval_count")
        if self.num_ctx and isinstance(prompt_tokens, int) and prompt_tokens >= self.num_ctx:
            logger.warning(
                f"Ollama 입력 토큰({prompt_tokens})이 num_ctx({self.num_ctx})에 도달했습니다. 프롬프트 앞부분이 잘렸을 수 있습니다."
            )

        if not wants_json:
            return content

        try:
            parsed = json.loads(self._strip_code_fence(content))
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"Ollama JSON 응답 파싱 실패, 원문 텍스트를 반환합니다: {content[:200]}")
            return content
        return self._coerce_to_schema(parsed, response_schema)

    async def list_models_async(self) -> List[str]:
        """`/api/tags`에서 서버에 설치된 모델 태그 목록을 조회합니다."""
        data = await self._request_async("GET", "/api/tags", timeout=15)
        names = []
        for m in data.get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                names.append(str(name))
        return sorted(set(names))

    async def check_health_async(self) -> Tuple[bool, str]:
        """서버 연결, 버전, 선택한 모델의 설치 여부를 점검합니다."""
        try:
            version_info = await self._request_async("GET", "/api/version", timeout=10)
            models = await self.list_models_async()
        except BtgApiClientException as e:
            return False, str(e)
        except Exception as e:
            return False, f"Ollama 연결 실패: {e}"

        version = version_info.get("version", "알 수 없음")
        base_msg = f"Ollama 서버 연결 성공 (v{version}, 설치된 모델 {len(models)}개)"

        if not self.model_name:
            return True, f"{base_msg}\n모델이 아직 선택되지 않았습니다."

        installed = set(models) | {m[: -len(":latest")] for m in models if m.endswith(":latest")}
        if self.model_name not in installed:
            return False, (
                f"{base_msg}\n그러나 모델 '{self.model_name}'이(가) 설치되어 있지 않습니다.\n"
                f"터미널에서 `ollama pull {self.model_name}`을 실행하세요."
            )
        return True, f"{base_msg}\n모델 '{self.model_name}' 사용 가능"
