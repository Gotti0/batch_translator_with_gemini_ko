"""
LLM Client Factory for Neo Batch Translator (BTG)

설정 딕셔너리(config)의 'llm_provider'에 따라 적절한 BaseLLMClient 인스턴스를
생성하여 반환합니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from infrastructure.base_client import BaseLLMClient
from infrastructure.gemini_client import GeminiClient, GeminiInvalidRequestException
from infrastructure.claude_cli_client import ClaudeCliClient
from infrastructure.codex_cli_client import CodexCliClient
from infrastructure.antigravity_cli_client import AntigravityCliClient
from infrastructure.OpenAICompatibleClient import OpenAICompatibleClient
from infrastructure.ollama_client import OllamaClient, DEFAULT_OLLAMA_BASE_URL
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class LLMClientFactory:
    """
    공급자 설정에 따라 LLM 클라이언트를 생성하는 팩토리 클래스.
    """

    SUPPORTED_PROVIDERS = [
        "gemini",
        "claude_cli",
        "codex_cli",
        "antigravity_cli",
        "openai_compatible",
        "ollama",
    ]

    @staticmethod
    def _is_service_account(value: Any) -> bool:
        """값이 서비스 계정 정보(dict 또는 JSON 문자열)인지 판정한다."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return False
        return isinstance(value, dict) and value.get("type") == "service_account"

    @classmethod
    def _read_service_account_file(cls, path: str) -> Optional[str]:
        """서비스 계정 JSON 파일 내용을 읽는다. 읽을 수 없거나 서비스 계정이 아니면 None."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"Vertex AI 서비스 계정 파일을 읽지 못했습니다 ({Path(path).name}): {e}")
            return None
        if not cls._is_service_account(text):
            logger.warning(f"Vertex AI 서비스 계정 파일이 아닙니다 ({Path(path).name})")
            return None
        return text

    @classmethod
    def create_client(
        cls,
        config: Dict[str, Any],
        auth_credentials: Optional[Any] = None,
        requests_per_minute: Optional[float] = None,
    ) -> BaseLLMClient:
        """
        설정에 맞는 클라이언트를 생성합니다.

        Args:
            config: 애플리케이션 설정 딕셔너리
            auth_credentials: API 키 또는 인증 정보 (선택 사항)
            requests_per_minute: 분당 요청 제한 수 (선택 사항)

        Returns:
            BaseLLMClient: 생성된 LLM 클라이언트 인스턴스
        """
        provider = str(config.get("llm_provider", "gemini")).lower().strip()

        if provider == "claude_cli":
            cli_path = config.get("claude_cli_path", "claude")
            model_name = config.get("claude_cli_model") or config.get("model_name")
            timeout = int(config.get("api_timeout", 180))
            # Gemini용 api_keys로 대체하지 않는다: 다른 회사 키가 ANTHROPIC_API_KEY로 새고 로그인 세션을 가린다
            api_key = config.get("claude_cli_api_key") or None
            logger.info("LLMClientFactory: ClaudeCliClient 생성")
            return ClaudeCliClient(
                cli_path=cli_path,
                model_name=model_name,
                api_key=api_key,
                timeout_seconds=timeout,
                effort=config.get("claude_cli_effort"),
            )

        elif provider == "codex_cli":
            cli_path = config.get("codex_cli_path", "codex")
            model_name = config.get("codex_cli_model") or config.get("model_name") or "gpt-5.5"
            timeout = int(config.get("api_timeout", 180))
            api_key = config.get("codex_cli_api_key") or None
            logger.info("LLMClientFactory: CodexCliClient 생성")
            return CodexCliClient(
                cli_path=cli_path,
                model_name=model_name,
                api_key=api_key,
                timeout_seconds=timeout,
                effort=config.get("codex_cli_effort"),
            )

        elif provider == "antigravity_cli":
            cli_path = config.get("antigravity_cli_path", "agy")
            model_name = config.get("antigravity_cli_model") or config.get("model_name")
            effort = config.get("antigravity_cli_effort")
            timeout = int(config.get("api_timeout", 180))
            logger.info("LLMClientFactory: AntigravityCliClient 생성")
            return AntigravityCliClient(
                cli_path=cli_path,
                model_name=model_name,
                effort=effort,
                timeout_seconds=timeout,
            )

        elif provider == "openai_compatible":
            base_url = config.get("openai_compatible_base_url", "")
            api_key = config.get("openai_compatible_api_key", "no-key")
            model_name = config.get("openai_compatible_model") or config.get("model_name")
            rpm = requests_per_minute if requests_per_minute is not None else config.get("requests_per_minute")
            timeout = int(config.get("api_timeout", 60))
            logger.info("LLMClientFactory: OpenAICompatibleClient 생성")
            return OpenAICompatibleClient(
                api_key=api_key,
                base_url=base_url,
                default_model=model_name,
                requests_per_minute=rpm,
                request_timeout=timeout,
                reasoning_effort=config.get("openai_compatible_reasoning_effort"),
            )

        elif provider == "ollama":
            logger.info("LLMClientFactory: OllamaClient 생성")
            return OllamaClient(
                base_url=config.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL,
                model_name=config.get("ollama_model"),
                api_key=config.get("ollama_api_key") or None,
                num_ctx=config.get("ollama_num_ctx", 16384),
                keep_alive=config.get("ollama_keep_alive"),
                timeout_seconds=float(config.get("api_timeout", 600.0)),
                think=config.get("ollama_think"),
            )

        else:
            # 기본값: GeminiClient
            use_vertex = config.get("use_vertex_ai", False)
            project = config.get("gcp_project")
            location = config.get("gcp_location")
            sa_path = config.get("service_account_file_path")

            if use_vertex:
                # Vertex에서는 API 키로 대체하지 않는다. 서비스 계정이 없으면 GeminiClient가 ADC를 쓴다.
                # GeminiClient는 문자열을 SA JSON 또는 API 키로 해석하므로 경로가 아니라 파일 내용을 넘긴다.
                # 헬스체크·모델 조회처럼 auth_credentials 없이 설정만 넘기는 호출도 있어 여기서 읽는다.
                sa_json = cls._read_service_account_file(sa_path) if sa_path else None
                if sa_json is not None:
                    creds = sa_json
                elif sa_path and not cls._is_service_account(auth_credentials):
                    # 경로를 지정했는데 못 읽으면 다른 계정(ADC)으로 조용히 넘어가지 않는다
                    raise GeminiInvalidRequestException(
                        f"Vertex AI 서비스 계정 파일을 사용할 수 없습니다: {Path(sa_path).name}"
                    )
                else:
                    creds = auth_credentials
            elif auth_credentials is not None:
                creds = auth_credentials
            else:
                creds = config.get("api_keys") or config.get("api_key")

            rpm = (
                requests_per_minute
                if requests_per_minute is not None
                else config.get("requests_per_minute", 2.0)
            )
            timeout = float(config.get("api_timeout", 60.0))

            logger.info("LLMClientFactory: GeminiClient 생성")
            return GeminiClient(
                auth_credentials=creds,
                project=project,
                location=location,
                requests_per_minute=rpm,
                api_timeout=timeout,
                overload_pause_threshold=config.get("overload_pause_threshold", 3),
                overload_pause_seconds=config.get("overload_pause_seconds", 300.0),
                overload_max_pause_seconds=config.get("overload_max_pause_seconds", 1800.0),
                use_vertex=bool(use_vertex),
            )
