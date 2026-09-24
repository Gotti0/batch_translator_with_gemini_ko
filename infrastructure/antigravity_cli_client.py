"""
Antigravity CLI Client Adapter for Neo Batch Translator (BTG)

사용자의 로컬 시스템에 설치된 Google Antigravity CLI(agy.exe)를
비동기 서브프로세스로 실행하여, 별도 API 키 없이 기존 AGY 세션으로
고속 번역을 수행하는 런타임 어댑터입니다.
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


class AntigravityCliClient(BaseLLMClient):
    """
    Google Antigravity CLI (`agy -p`) 서브프로세스 래퍼 클라이언트.
    """

    FALLBACK_MODELS = [
        "default",
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.1-pro-high",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ]

    def __init__(
        self,
        cli_path: str = "agy",
        model_name: Optional[str] = None,
        effort: Optional[str] = None,
        timeout_seconds: int = 180,
    ) -> None:
        """
        AntigravityCliClient를 초기화합니다.

        Args:
            cli_path: agy 실행 파일 경로 또는 커맨드 이름
            model_name: 사용할 모델명 (None 또는 'default'이면 기본값 사용)
            effort: 추론 강도 ('low', 'medium', 'high', 선택 사항)
            timeout_seconds: 서브프로세스 실행 타임아웃(초)
        """
        resolved_path = shutil.which(cli_path)
        self.cli_path = resolved_path if resolved_path else cli_path
        self.model_name = model_name if model_name and model_name != "default" else None
        self.effort = effort if effort in ("low", "medium", "high") else None
        self.timeout_seconds = timeout_seconds

        logger.info(
            f"AntigravityCliClient 초기화 완료 (실행 경로: {self.cli_path}, 모델: {self.model_name or 'AGY 기본값'}, 추론 강도: {self.effort or '기본값'})"
        )

    @property
    def provider_name(self) -> str:
        return "antigravity_cli"

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
        `agy -p - --output-format json` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        prompt_str = self._prepare_prompt_string(prompt, system_instruction)

        cmd = [
            self.cli_path,
            "-p",
            "-",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]

        if self.model_name:
            cmd.extend(["--model", self.model_name])

        if self.effort:
            cmd.extend(["--effort", self.effort])

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
                    cmd.extend(["--json-schema", schema_temp_path])
                except Exception as e_schema:
                    logger.warning(f"AGY JSON Schema 임시 파일 생성 실패: {e_schema}")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        logger.debug(f"Antigravity CLI 실행 시작: {' '.join(cmd[:4])} ...")

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
                    f"Antigravity CLI 비정상 종료 (code: {proc.returncode}): {stderr_text or stdout_text}"
                )
                err_msg = stderr_text or stdout_text
                if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                    raise BtgApiRateLimitException(f"Antigravity CLI 사용량 제한: {err_msg}")
                if "login" in err_msg.lower() or "auth" in err_msg.lower():
                    raise BtgApiClientException(
                        f"Antigravity CLI 인증 필요. 터미널에서 `agy`를 실행하여 로그인하세요: {err_msg}"
                    )
                raise BtgApiClientException(
                    f"Antigravity CLI 실행 실패 (code {proc.returncode}): {err_msg}"
                )

            # JSON 출력 파싱 시도
            try:
                data = json.loads(stdout_text)
                if isinstance(data, dict):
                    if data.get("status") == "ERROR":
                        raise BtgApiClientException(
                            f"Antigravity CLI 응답 오류: {data.get('response') or stdout_text}"
                        )
                    resp = data.get("response")
                    if resp is not None:
                        return str(resp).strip()
            except json.JSONDecodeError:
                pass

            return stdout_text

        except asyncio.TimeoutError as e:
            logger.error(f"Antigravity CLI 실행 시간 초과 ({self.timeout_seconds}초)")
            try:
                proc.kill()
            except Exception:
                pass
            raise BtgApiClientException(
                f"Antigravity CLI 실행 시간 초과 ({self.timeout_seconds}초)", original_exception=e
            ) from e
        except FileNotFoundError as e:
            raise BtgApiClientException(
                f"Antigravity CLI 실행 파일을 찾을 수 없습니다: {self.cli_path}", original_exception=e
            ) from e
        finally:
            if schema_temp_path and os.path.exists(schema_temp_path):
                try:
                    os.remove(schema_temp_path)
                except OSError:
                    pass

    async def list_models_async(self) -> List[str]:
        """`agy models` 명령어를 호출하여 동적으로 지원 모델 목록 반환"""
        resolved_path = shutil.which(self.cli_path) or self.cli_path
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return list(self.FALLBACK_MODELS)

        try:
            proc = await asyncio.create_subprocess_exec(
                resolved_path,
                "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                output = stdout_data.decode("utf-8", errors="replace")
                models = []
                for line in output.splitlines():
                    line = line.strip()
                    if not line or "Fetching" in line:
                        continue
                    parts = line.split("\t")
                    model_id = parts[0].strip()
                    if model_id and not model_id.startswith("#"):
                        models.append(model_id)
                if models:
                    return ["default"] + models
        except Exception as e:
            logger.warning(f"agy models 조회 실패, 폴백 사용: {e}")

        return list(self.FALLBACK_MODELS)

    async def check_health_async(self) -> tuple[bool, str]:
        """
        Antigravity CLI 인증 및 실행 가능 상태를 점검합니다.
        """
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return (
                False,
                f"Antigravity CLI 실행 파일을 찾을 수 없습니다: '{self.cli_path}'.\n"
                f"Antigravity CLI(agy)가 설치되어 있는지 확인하거나, 설정에서 실행 파일의 전체 경로를 지정해 주세요.",
            )

        try:
            test_prompt = "Say 'OK' in one word."
            response = await asyncio.wait_for(
                self.generate_text_async(test_prompt),
                timeout=60,
            )
            clean_resp = response.replace("\n", " ").strip()
            return True, f"Google Antigravity CLI 인증 성공 (AGY 세션 정상 활성화됨. 응답: {clean_resp[:30]})"
        except asyncio.TimeoutError:
            return False, "Antigravity CLI 연결 시간 초과 (60초 내 응답 없음)."
        except BtgApiClientException as e:
            msg = str(e)
            if "login" in msg.lower() or "auth" in msg.lower():
                return (
                    False,
                    f"Antigravity CLI 인증이 필요합니다.\n"
                    f"터미널(PowerShell/CMD)을 열고 'agy'를 실행하여 로그인을 완료해 주세요.\n"
                    f"(세부 오류: {msg})",
                )
            return False, f"Antigravity CLI 인증/실행 실패: {msg}"
        except Exception as e:
            return False, f"Antigravity CLI 점검 중 예외 발생: {e}"
