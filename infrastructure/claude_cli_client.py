"""
Claude Code CLI Client Adapter for Neo Batch Translator (BTG)

사용자의 로컬 시스템에 설치된 Claude Code CLI(claude.exe)를
비동기 서브프로세스로 실행하여, 별도 API 키 없이 기존 구독(Pro/Max) 크레딧으로
번역을 수행하는 런타임 어댑터입니다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Dict, List, Optional, Union

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiInvalidRequestException,
)
from infrastructure.base_client import BaseLLMClient, kill_if_running
from infrastructure.reasoning_options import CLAUDE_CLI
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class ClaudeCliClient(BaseLLMClient):
    """
    Anthropic Claude Code CLI (`claude`) 서브프로세스 래퍼 클라이언트.
    """

    DEFAULT_MODELS = [
        "default",
        "claude-sonnet-5",
        "claude-opus-5-5",
        "claude-haiku-4-5-20251001",
        "claude-fable-5-1",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]

    def __init__(
        self,
        cli_path: str = "claude",
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: int = 180,
        effort: Optional[str] = None,
    ) -> None:
        """
        ClaudeCliClient를 초기화합니다.

        Args:
            cli_path: claude 실행 파일 경로 또는 커맨드 이름
            model_name: 사용할 모델명 (None 또는 'default'이면 CLI 기본값 사용)
            api_key: Anthropic API 키 오버라이드 (선택 사항, 미지정 시 로컬 구독 세션 사용)
            timeout_seconds: 서브프로세스 실행 타임아웃(초)
            effort: 추론 강도 (`--effort`, 허용 값은 reasoning_options.CLAUDE_CLI). None이면 CLI 기본값
        """
        resolved_path = shutil.which(cli_path)
        self.cli_path = resolved_path if resolved_path else cli_path
        self.model_name = model_name if model_name and model_name != "default" else None
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.effort = CLAUDE_CLI.normalize(effort)

        logger.info(
            f"ClaudeCliClient 초기화 완료 (실행 경로: {self.cli_path}, 모델: {self.model_name or 'CLI 기본값'}, "
            f"추론 강도: {self.effort or 'CLI 기본값'}, API 키 지정 여부: {bool(self.api_key)})"
        )

    @property
    def provider_name(self) -> str:
        return "claude_cli"

    @property
    def supports_pagefold(self) -> bool:
        return False

    def _prepare_prompt_string(self, prompt: Union[str, Any]) -> str:
        """입력 프롬프트를 단일 텍스트 문자열로 정규화합니다."""
        if isinstance(prompt, str):
            return prompt
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
            return "\n\n".join(parts)
        return str(prompt)

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
        `claude -p` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        prompt_str = self._prepare_prompt_string(prompt)

        # JSON 스키마 요구사항이 있는 경우 프롬프트에 명시적 지침 추가
        if response_schema is not None:
            schema_json = ""
            if hasattr(response_schema, "model_json_schema"):
                schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
            elif isinstance(response_schema, dict):
                schema_json = json.dumps(response_schema, ensure_ascii=False)
            else:
                schema_json = str(response_schema)
            
            schema_instruction = (
                f"\n\n[OUTPUT REQUIREMENT]: Respond with ONLY a valid JSON object strictly matching the following schema. "
                f"Do not include markdown code block formatting (e.g. ```json) or preamble:\n{schema_json}"
            )
            prompt_str = prompt_str + schema_instruction

        # 커맨드라인 구성: 도구 실행 차단(--tools "") 및 권한 확인 생략
        cmd = [
            self.cli_path,
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--dangerously-skip-permissions",
        ]

        if self.model_name:
            cmd.extend(["--model", self.model_name])

        if self.effort:
            cmd.extend(["--effort", self.effort])

        if system_instruction:
            cmd.extend(["--system-prompt", str(system_instruction).strip()])

        # 서브프로세스 환경 변수 (UTF-8 인코딩 강제 및 API 키 오버라이드)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key

        logger.debug(f"Claude CLI 실행 시작: {' '.join(cmd[:4])} ...")

        proc = None
        try:
            # stdin 파이프로 대용량 텍스트를 안전하게 주입
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
                    f"Claude CLI 비정상 종료 (code: {proc.returncode}): {stderr_text or stdout_text}"
                )
                err_msg = stderr_text or stdout_text
                if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                    raise BtgApiRateLimitException(f"Claude CLI 사용량 제한: {err_msg}")
                if "login" in err_msg.lower() or "auth" in err_msg.lower():
                    raise BtgApiClientException(
                        f"Claude CLI 인증 실패. 터미널에서 `claude`를 실행하여 로그인하세요: {err_msg}"
                    )
                raise BtgApiClientException(
                    f"Claude CLI 실행 실패 (code {proc.returncode}): {err_msg}"
                )

            # JSON 출력 파싱 시도
            try:
                data = json.loads(stdout_text)
                if isinstance(data, dict):
                    if data.get("is_error"):
                        raise BtgApiClientException(
                            f"Claude CLI 응답 오류: {data.get('result') or stdout_text}"
                        )
                    result_text = data.get("result", "")
                    if result_text:
                        return str(result_text).strip()
            except json.JSONDecodeError:
                pass

            # JSON 파싱 실패 시 원문 텍스트 반환
            return stdout_text

        except asyncio.CancelledError:
            # 바깥 wait_for(헬스체크 제한 시간)나 번역 중지로 취소되면 안쪽 TimeoutError 경로를 타지 않는다.
            # 그대로 두면 CLI 프로세스가 살아남아 호출을 계속하므로 여기서 정리한다.
            kill_if_running(proc)
            raise
        except asyncio.TimeoutError as e:
            logger.error(f"Claude CLI 실행 시간 초과 ({self.timeout_seconds}초)")
            kill_if_running(proc)
            raise BtgApiClientException(
                f"Claude CLI 실행 시간 초과 ({self.timeout_seconds}초)", original_exception=e
            ) from e
        except FileNotFoundError as e:
            raise BtgApiClientException(
                f"Claude CLI 실행 파일을 찾을 수 없습니다: {self.cli_path}", original_exception=e
            ) from e

    async def list_models_async(self) -> List[str]:
        """
        Claude Code 로컬 모델 카탈로그 캐시(~/.claude/cache/model-catalog/*.json)에서
        동적으로 사용 가능한 모델 목록을 읽어옵니다.
        캐시가 없거나 읽기 실패 시 DEFAULT_MODELS로 폴백합니다.
        """
        import glob

        catalog_dir = os.path.expanduser("~/.claude/cache/model-catalog")
        if os.path.exists(catalog_dir):
            try:
                pattern = os.path.join(catalog_dir, "*.json")
                files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
                for f in files:
                    try:
                        with open(f, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                        models_raw = (
                            data.get("catalog", {}).get("config", {}).get("models", [])
                        )
                        if models_raw:
                            model_ids = [
                                m.get("id")
                                for m in models_raw
                                if isinstance(m, dict) and m.get("id")
                            ]
                            if model_ids:
                                return ["default"] + model_ids
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"Claude model-catalog 캐시 읽기 실패: {e}")

        return list(self.DEFAULT_MODELS)

    async def check_health_async(self) -> tuple[bool, str]:
        """
        Claude Code CLI 인증 및 실행 가능 상태를 점검합니다.
        """
        # 실행 파일 존재 확인
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return (
                False,
                f"Claude CLI 실행 파일을 찾을 수 없습니다: '{self.cli_path}'.\n"
                f"Claude Code가 설치되어 있는지 확인하거나, 설정에서 실행 파일의 전체 경로를 지정해 주세요.",
            )

        try:
            # 빠른 응답 테스트 프롬프트
            test_prompt = "Say 'OK' in one word."
            response = await asyncio.wait_for(
                self.generate_text_async(test_prompt),
                timeout=20,
            )
            auth_source = "API 키" if self.api_key else "로컬 로그인 세션"
            clean_resp = response.replace("\n", " ").strip()
            return True, f"Claude Code 인증 성공 ({auth_source} 정상 활성화됨. 응답: {clean_resp[:30]})"
        except asyncio.TimeoutError:
            return False, "Claude CLI 연결 시간 초과 (20초 내 응답 없음)."
        except BtgApiClientException as e:
            msg = str(e)
            if "login" in msg.lower() or "auth" in msg.lower():
                return (
                    False,
                    f"Claude Code 로그인이 필요합니다.\n"
                    f"터미널(PowerShell/CMD)을 열고 'claude'를 실행하여 브라우저 로그인을 완료해 주세요.\n"
                    f"(또는 API 키를 입력하여 실행할 수도 있습니다.)",
                )
            return False, f"Claude CLI 인증/실행 실패: {msg}"
        except Exception as e:
            return False, f"Claude CLI 점검 중 예외 발생: {e}"
