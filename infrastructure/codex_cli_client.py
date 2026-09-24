"""
Codex CLI Client Adapter for Neo Batch Translator (BTG)

사용자의 로컬 시스템에 설치된 OpenAI Codex CLI(codex.exe)를
비동기 서브프로세스로 실행하여, 별도 API 키 없이 기존 ChatGPT Plus 계정
세션으로 번역을 수행하는 런타임 어댑터입니다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Union

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiInvalidRequestException,
)
from infrastructure.base_client import BaseLLMClient
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class CodexCliClient(BaseLLMClient):
    """
    OpenAI Codex CLI (`codex exec`) 서브프로세스 래퍼 클라이언트.
    """

    FALLBACK_MODELS = [
        "default",
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "o3",
        "o3-mini",
        "gpt-4o",
    ]

    def __init__(
        self,
        cli_path: str = "codex",
        model_name: Optional[str] = "gpt-5.5",
        api_key: Optional[str] = None,
        timeout_seconds: int = 180,
    ) -> None:
        """
        CodexCliClient를 초기화합니다.

        Args:
            cli_path: codex 실행 파일 경로 또는 커맨드 이름
            model_name: 사용할 모델명 (None 또는 'default'이면 기본값 'gpt-5.5')
            api_key: OpenAI API 키 오버라이드 (선택 사항, 미지정 시 ChatGPT Plus 세션 사용)
            timeout_seconds: 서브프로세스 실행 타임아웃(초)
        """
        resolved_path = shutil.which(cli_path)
        self.cli_path = resolved_path if resolved_path else cli_path
        self.model_name = model_name if model_name and model_name != "default" else "gpt-5.5"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

        logger.info(
            f"CodexCliClient 초기화 완료 (실행 경로: {self.cli_path}, 모델: {self.model_name}, API 키 지정 여부: {bool(self.api_key)})"
        )

    @property
    def provider_name(self) -> str:
        return "codex_cli"

    @property
    def supports_pagefold(self) -> bool:
        return False

    def _prepare_prompt_string(
        self, prompt: Union[str, Any], system_instruction: Optional[str] = None
    ) -> str:
        """프롬프트와 시스템 지침을 결합하여 문자열을 준비합니다."""
        body = ""
        if isinstance(prompt, str):
            body = prompt
        elif isinstance(prompt, list):
            parts = []
            for item in prompt:
                if isinstance(item, dict):
                    role = item.get("role", "user")
                    text = item.get("text") or item.get("content") or ""
                    parts.append(f"[{role.upper()}]:\n{text}")
                elif hasattr(item, "parts"):
                    subparts = []
                    for p in item.parts:
                        if hasattr(p, "text"):
                            subparts.append(str(p.text))
                        elif isinstance(p, str):
                            subparts.append(p)
                    role = getattr(item, "role", "user")
                    parts.append(f"[{str(role).upper()}]:\n{''.join(subparts)}")
                elif hasattr(item, "text"):
                    parts.append(str(item.text))
                else:
                    parts.append(str(item))
            body = "\n\n".join(parts)
        else:
            body = str(prompt)

        if system_instruction:
            return f"System Instructions:\n{system_instruction.strip()}\n\nUser Input:\n{body}"
        return body

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
        `codex exec - --ephemeral --json` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        prompt_str = self._prepare_prompt_string(prompt, system_instruction)

        cmd = [
            self.cli_path,
            "exec",
            "-",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]

        if self.model_name:
            cmd.extend(["-m", self.model_name])

        schema_temp_path = None
        if response_schema is not None:
            schema_dict = None
            if hasattr(response_schema, "model_json_schema"):
                schema_dict = response_schema.model_json_schema()
            elif isinstance(response_schema, dict):
                schema_dict = response_schema

            if schema_dict:
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", suffix=".json", delete=False, encoding="utf-8"
                    ) as f:
                        json.dump(schema_dict, f, ensure_ascii=False)
                        schema_temp_path = f.name
                    cmd.extend(["--output-schema", schema_temp_path])
                except Exception as e_schema:
                    logger.warning(f"Codex JSON Schema 임시 파일 생성 실패: {e_schema}")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if self.api_key:
            env["OPENAI_API_KEY"] = self.api_key

        logger.debug(f"Codex CLI 실행 시작: {' '.join(cmd[:4])} ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(input=prompt_str.encode("utf-8")),
                timeout=self.timeout_seconds,
            )

            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.error(
                    f"Codex CLI 비정상 종료 (code: {proc.returncode}): {stderr_text or stdout_text}"
                )
                err_msg = stderr_text or stdout_text
                if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                    raise BtgApiRateLimitException(f"Codex CLI 사용량 제한: {err_msg}")
                if "login" in err_msg.lower() or "auth" in err_msg.lower():
                    raise BtgApiClientException(
                        f"Codex CLI 인증 필요. 터미널에서 `codex login`을 실행하세요: {err_msg}"
                    )
                raise BtgApiClientException(
                    f"Codex CLI 실행 실패 (code {proc.returncode}): {err_msg}"
                )

            # JSON Lines 파싱하여 agent_message 텍스트 추출
            messages: List[str] = []
            for line in stdout_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        item = event.get("item")
                        if isinstance(item, dict) and item.get("type") == "agent_message":
                            text = item.get("text")
                            if text:
                                messages.append(text)
                except json.JSONDecodeError:
                    continue

            if messages:
                return "\n".join(messages).strip()

            # agent_message가 추출되지 않은 경우 raw stdout에서 반환
            return stdout_text

        except asyncio.TimeoutError as e:
            logger.error(f"Codex CLI 실행 시간 초과 ({self.timeout_seconds}초)")
            try:
                proc.kill()
            except Exception:
                pass
            raise BtgApiClientException(
                f"Codex CLI 실행 시간 초과 ({self.timeout_seconds}초)", original_exception=e
            ) from e
        except FileNotFoundError as e:
            raise BtgApiClientException(
                f"Codex CLI 실행 파일을 찾을 수 없습니다: {self.cli_path}", original_exception=e
            ) from e
        finally:
            if schema_temp_path and os.path.exists(schema_temp_path):
                try:
                    os.remove(schema_temp_path)
                except OSError:
                    pass

    async def list_models_async(self) -> List[str]:
        """Codex 로컬 캐시 또는 기본 모델 목록 반환"""
        cache_path = os.path.expanduser("~/.codex/models_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    models = [
                        m.get("slug")
                        for m in data.get("models", [])
                        if isinstance(m, dict) and m.get("slug")
                    ]
                    if models:
                        return ["default"] + models
            except Exception as e:
                logger.warning(f"Codex models_cache.json 읽기 실패: {e}")

        return list(self.FALLBACK_MODELS)

    async def check_health_async(self) -> tuple[bool, str]:
        """
        OpenAI Codex CLI 인증 및 실행 가능 상태를 점검합니다.
        """
        # 실행 파일 존재 확인
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return (
                False,
                f"Codex CLI 실행 파일을 찾을 수 없습니다: '{self.cli_path}'.\n"
                f"Codex CLI가 설치되어 있는지 확인하거나, 설정에서 실행 파일의 전체 경로를 지정해 주세요.",
            )

        # 인증 상태 사전 점검 (~/.codex/auth.json 또는 API 키)
        auth_file = os.path.expanduser("~/.codex/auth.json")
        has_auth_file = os.path.exists(auth_file)
        has_api_key = bool(self.api_key or os.environ.get("OPENAI_API_KEY"))

        if not has_auth_file and not has_api_key:
            return (
                False,
                "Codex 인증 정보가 발견되지 않았습니다.\n"
                "터미널(PowerShell/CMD)을 열고 'codex login'을 실행하여 브라우저 로그인을 완료해 주세요.\n"
                "(또는 API 키를 입력하여 실행할 수도 있습니다.)",
            )

        try:
            # 빠른 응답 테스트 프롬프트
            test_prompt = "Say 'OK' in one word."
            response = await asyncio.wait_for(
                self.generate_text_async(test_prompt),
                timeout=20,
            )
            auth_source = "API 키" if self.api_key else "ChatGPT 세션"
            clean_resp = response.replace("\n", " ").strip()
            return True, f"OpenAI Codex CLI 인증 성공 ({auth_source} 정상 활성화됨. 응답: {clean_resp[:30]})"
        except asyncio.TimeoutError:
            return False, "Codex CLI 연결 시간 초과 (20초 내 응답 없음)."
        except BtgApiClientException as e:
            msg = str(e)
            if "login" in msg.lower() or "auth" in msg.lower():
                return (
                    False,
                    f"Codex CLI 인증이 만료되었거나 필요합니다.\n"
                    f"터미널(PowerShell/CMD)에서 'codex login'을 실행해 주세요.\n"
                    f"(세부 오류: {msg})",
                )
            return False, f"Codex CLI 인증/실행 실패: {msg}"
        except Exception as e:
            return False, f"Codex CLI 점검 중 예외 발생: {e}"
