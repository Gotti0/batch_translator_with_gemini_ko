"""
Base LLM Client Interface for Neo Batch Translator (BTG)

모든 AI 공급자(Gemini, Claude CLI, Codex CLI, OpenAI Compatible 등)의
비동기 텍스트 생성 인터페이스를 추상화합니다.
"""

from __future__ import annotations

import asyncio
import copy
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union



def kill_if_running(proc: Optional[asyncio.subprocess.Process]) -> None:
    """아직 끝나지 않은 CLI 서브프로세스를 종료한다.

    CLI 클라이언트는 호출이 취소(바깥 wait_for 제한 시간, 번역 중지)되거나 제한 시간을 넘길 때
    이것으로 프로세스를 정리한다. 취소는 안쪽 TimeoutError 경로를 타지 않으므로 따로 불러야 한다.
    """
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


CONTENT_SAFETY_PATTERNS = (
    "안전 필터",
    "안전 정책",
    "정책 위반",
    "prohibited use policy",
    "generative ai prohibited",
    "acceptable use policy",
    "safety filter",
    "content filter",
    "content_filter",
    "safety policy",
    "content safety",
    "blocked by safety",
    "safety reasons",
    "safety guideline",
    "safety guidelines",
    "responsible ai",
    "harmful content",
    "prohibited content",
    "sensitive content",
    "성적으로 노골적",
    "use-policy",
    "finish_reason: safety",
    "finish_reason': 'safety",
    'finish_reason": "safety',
)


def is_content_safety_error(err_msg: str) -> bool:
    """콘텐츠 안전 정책 위반 또는 검열(Content Safety/Filter) 오류인지 판별한다."""
    if not err_msg:
        return False
    lowered = err_msg.lower()
    return any(p in lowered for p in CONTENT_SAFETY_PATTERNS)


def resolve_call_options(
    system_instruction: Optional[str],
    response_schema: Optional[Any],
    kwargs: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Any]]:
    """시스템 지침과 응답 스키마를 호출 인자에서 찾는다.

    도메인 서비스는 GeminiClient 시그니처로 부른다: 지침은 `system_instruction_text`,
    스키마는 `generation_config_dict["response_schema"]`. 이 이름을 보지 않으면 **kwargs로 들어가
    조용히 버려진다 (CLI 클라이언트에서 프리필 지침·번역 기억·스키마가 빠졌던 원인).
    """
    system_text = system_instruction or kwargs.get("system_instruction_text")
    if response_schema is None:
        gen_config = kwargs.get("generation_config_dict") or {}
        response_schema = gen_config.get("response_schema")
    return system_text, response_schema


def schema_to_dict(response_schema: Any) -> Optional[Dict[str, Any]]:
    """Pydantic 모델, `list[Model]` 같은 타입, dict 스키마를 JSON Schema dict로 바꾼다."""
    if response_schema is None:
        return None
    if isinstance(response_schema, dict):
        return copy.deepcopy(response_schema)
    if hasattr(response_schema, "model_json_schema"):
        return response_schema.model_json_schema()
    try:
        from pydantic import TypeAdapter

        return TypeAdapter(response_schema).json_schema()
    except Exception:
        return None


ARRAY_WRAPPER_KEY = "items"


def wrap_array_schema(schema: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """최상위가 배열인 스키마를 `{"items": [...]}` 객체로 감싼다.

    Claude `--json-schema`, Codex `--output-schema`, agy `--json-schema`는 모두 최상위 object만
    받는다 (array면 400). `$defs`는 최상위로 올려야 `#/$defs/...` 참조가 유지된다.
    반환: (스키마, 감쌌는지)
    """
    if schema.get("type") != "array":
        return schema, False
    schema = dict(schema)
    defs = schema.pop("$defs", None) or schema.pop("definitions", None)
    wrapped: Dict[str, Any] = {
        "type": "object",
        "properties": {ARRAY_WRAPPER_KEY: schema},
        "required": [ARRAY_WRAPPER_KEY],
    }
    if defs:
        wrapped["$defs"] = defs
    return wrapped, True


def unwrap_array_result(data: Any) -> Any:
    """wrap_array_schema로 감싼 응답에서 배열을 꺼낸다."""
    if isinstance(data, dict) and isinstance(data.get(ARRAY_WRAPPER_KEY), list):
        return data[ARRAY_WRAPPER_KEY]
    return data


class BaseLLMClient(ABC):
    """
    모든 LLM 클라이언트의 기반 추상 클래스.
    """

    @abstractmethod
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
        비동기 텍스트 생성 호출.

        Args:
            prompt: 사용자 입력 프롬프트 (문자열 또는 멀티턴 리스트)
            system_instruction: 시스템 지시문
            temperature: 무작위성 조절 파라미터
            top_p: Nucleus sampling 파라미터
            response_schema: 구조화 출력용 JSON Schema (Pydantic 클래스 또는 dict)
            multimodal_parts: 멀티모달 Part 리스트 (Gemini PDF 등)
            **kwargs: 공급자별 확장 옵션

        Returns:
            str: 모델의 출력 텍스트
        """
        pass

    @abstractmethod
    async def list_models_async(self) -> List[str]:
        """
        사용 가능한 모델 식별자 목록을 반환합니다.
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        공급자 식별 이름 (예: 'gemini', 'claude_cli', 'codex_cli', 'openai_compatible', 'ollama')
        """
        pass

    @property
    def supports_pagefold(self) -> bool:
        """
        PageFold PDF 압축 Part를 네이티브로 지원하는지 여부.
        기본값은 False이며, Gemini 공급자만 True를 반환합니다.
        """
        return False

    async def check_health_async(self) -> tuple[bool, str]:
        """
        인증 및 통신 상태를 비동기로 점검합니다.

        Returns:
            tuple[bool, str]: (성공 여부, 진단 결과 메시지)
        """
        try:
            models = await self.list_models_async()
            return True, f"{self.provider_name} 연결 성공 ({len(models)}개 모델 감지)"
        except Exception as e:
            return False, f"{self.provider_name} 연결/인증 실패: {e}"
