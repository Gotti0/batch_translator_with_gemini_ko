"""
Base LLM Client Interface for Neo Batch Translator (BTG)

모든 AI 공급자(Gemini, Claude CLI, Codex CLI, OpenAI Compatible 등)의
비동기 텍스트 생성 인터페이스를 추상화합니다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


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
