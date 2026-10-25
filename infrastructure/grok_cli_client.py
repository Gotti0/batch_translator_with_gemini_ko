"""
Grok Build CLI Client Adapter for Neo Batch Translator (BTG)

사용자의 로컬 시스템에 설치된 xAI Grok Build CLI(grok.exe)를
비동기 서브프로세스로 실행하여, 별도 API 키 없이 로그인된 grok.com 세션으로
번역을 수행하는 런타임 어댑터입니다.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Union

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiContentSafetyException,
)
from infrastructure.base_client import (
    BaseLLMClient,
    kill_if_running,
    is_content_safety_error,
    resolve_call_options,
    schema_to_dict,
)
from infrastructure.reasoning_options import GROK_CLI
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

# Windows CreateProcess 명령줄 최대 길이(널 문자 제외)
WINDOWS_MAX_COMMAND_LINE = 32766

# grok은 Claude·Cursor의 규칙(CLAUDE.md, ~/.claude/rules)·스킬·훅·MCP·에이전트를 기본으로 읽는다.
# 끄지 않으면 사용자 지침이 번역 문맥에 섞이고 ~/.claude의 Stop 훅이 호출마다 실행된다 (2026-10-25 실측).
COMPAT_ENV_VARS = tuple(
    f"GROK_{vendor}_{surface}_ENABLED"
    for vendor in ("CLAUDE", "CURSOR")
    for surface in ("AGENTS", "RULES", "SKILLS", "HOOKS", "MCPS")
)

# 기본 도구 27개(셸·쓰기·웹·이미지 생성 포함) 대신 읽기 하나만 남긴다. 없는 도구 이름을 주면 grok은 경고만 하고
# 모든 도구를 켠 채 실행하므로 이 값은 실제 도구 ID여야 한다 (2026-10-25 실측).
ALLOWED_TOOLS = "read_file"

# 사용자 ~/.grok/sessions에 호출마다 파일이 쌓이지 않도록 쓰는 전용 홈. 처음 실행 때 번들 파일 15MB를
# 풀어 놓으므로 호출마다 새로 만들지 않고 계속 쓴다.
APP_GROK_HOME = os.path.join(tempfile.gettempdir(), "btg_grok_home")
APP_GROK_CONFIG = "[storage]\n# 하루 넘게 쓰지 않은 세션 폴더는 grok이 지운다\ncleanup_ttl_days = 1\n"


def _user_grok_home() -> str:
    return os.environ.get("GROK_HOME") or os.path.expanduser(os.path.join("~", ".grok"))


def _is_rate_limited(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(k in lowered for k in ("rate limit", "too many requests", "usage limit", "quota", "code 429"))


def _is_auth_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return any(k in lowered for k in ("login", "auth", "unauthorized", "not logged in"))


def build_isolated_env() -> Dict[str, str]:
    """사용자 환경이 번역 호출에 섞이지 않게 하는 grok 실행 환경."""
    os.makedirs(APP_GROK_HOME, exist_ok=True)
    config_path = os.path.join(APP_GROK_HOME, "config.toml")
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(APP_GROK_CONFIG)

    env = os.environ.copy()
    for name in COMPAT_ENV_VARS:
        env[name] = "false"
    env["GROK_HOME"] = APP_GROK_HOME
    # 홈을 바꿔도 로그인은 사용자의 grok.com 세션을 그대로 쓴다
    env["GROK_AUTH_PATH"] = os.path.join(_user_grok_home(), "auth.json")
    return env


def parse_model_list(output: str) -> List[str]:
    """`grok models` 출력(`  * grok-4.7 (default)`, `  - grok-4.6`)에서 모델 ID만 뽑는다."""
    models: List[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped[:2] not in ("* ", "- "):
            continue
        model_id = stripped[2:].split(" ", 1)[0].strip()
        if model_id and model_id not in models:
            models.append(model_id)
    return models


class GrokCliClient(BaseLLMClient):
    """
    xAI Grok Build CLI (`grok --prompt-file`) 서브프로세스 래퍼 클라이언트.
    """

    FALLBACK_MODELS = [
        "default",
        "grok-4.7",
        "grok-4.7-build-fast",
        "grok-4.6",
        "grok-4.5",
    ]

    def __init__(
        self,
        cli_path: str = "grok",
        model_name: Optional[str] = None,
        effort: Optional[str] = None,
        timeout_seconds: int = 180,
    ) -> None:
        """
        GrokCliClient를 초기화합니다.

        Args:
            cli_path: grok 실행 파일 경로 또는 커맨드 이름
            model_name: 사용할 모델 ID (None 또는 'default'이면 grok 기본값)
            effort: 추론 강도 (`--effort`, 허용 값은 reasoning_options.GROK_CLI, 선택 사항)
            timeout_seconds: 서브프로세스 실행 타임아웃(초)
        """
        resolved_path = shutil.which(cli_path)
        if not resolved_path and cli_path == "grok":
            # 설치 직후에는 PATH가 갱신되지 않은 프로세스가 많아 기본 설치 위치도 본다
            default_path = os.path.join(_user_grok_home(), "bin", "grok.exe" if os.name == "nt" else "grok")
            if os.path.exists(default_path):
                resolved_path = default_path
        self.cli_path = resolved_path if resolved_path else cli_path
        self.model_name = model_name if model_name and model_name != "default" else None
        self.effort = GROK_CLI.normalize(effort)
        self.timeout_seconds = timeout_seconds

        logger.info(
            f"GrokCliClient 초기화 완료 (실행 경로: {self.cli_path}, 모델: {self.model_name or 'grok 기본값'}, "
            f"추론 강도: {self.effort or '기본값'})"
        )

    @property
    def provider_name(self) -> str:
        return "grok_cli"

    @property
    def supports_pagefold(self) -> bool:
        return False

    def _is_available(self) -> bool:
        return bool(shutil.which(self.cli_path)) or os.path.exists(self.cli_path)

    @staticmethod
    def _prepare_prompt_string(prompt: Union[str, Any]) -> str:
        """입력 프롬프트를 단일 텍스트 문자열로 정규화합니다."""
        if isinstance(prompt, str):
            return prompt
        if isinstance(prompt, list):
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

    def _base_command(self) -> List[str]:
        cmd = [
            self.cli_path,
            "--verbatim",
            "--output-format",
            "json",
            "--tools",
            ALLOWED_TOOLS,
            "--no-subagents",
            "--disable-web-search",
            "--no-plan",
            "--no-auto-update",
        ]
        if self.model_name:
            cmd.extend(["-m", self.model_name])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        return cmd

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
        `grok --prompt-file <tmp> --verbatim --output-format json` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        system_instruction, response_schema = resolve_call_options(system_instruction, response_schema, kwargs)
        prompt_str = self._prepare_prompt_string(prompt)
        # grok --json-schema는 최상위 array를 그대로 받는다 (Claude·Codex와 달리 감쌀 필요 없음, 실측)
        schema_dict = schema_to_dict(response_schema)

        proc = None
        tmp_root = None
        try:
            tmp_root = tempfile.mkdtemp(prefix="btg_grok_")
            workspace = os.path.join(tmp_root, "workspace")
            os.mkdir(workspace)
            prompt_path = os.path.join(tmp_root, "prompt.txt")

            cmd = self._base_command() + ["--cwd", workspace, "--prompt-file", prompt_path]
            if schema_dict:
                # 파일 경로는 받지 않고 JSON 문자열만 받는다 (실측)
                cmd.extend(["--json-schema", json.dumps(schema_dict, ensure_ascii=False)])

            if system_instruction:
                system_text = str(system_instruction).strip()
                with_flag = cmd + ["--system-prompt-override", system_text]
                if os.name == "nt" and len(subprocess.list2cmdline(with_flag)) > WINDOWS_MAX_COMMAND_LINE:
                    # 번역 기억 블록 등으로 지침이 길면 명령줄 대신 프롬프트 파일 앞에 붙인다
                    logger.warning(
                        f"Grok CLI 시스템 지침({len(system_text)}자)이 명령줄 상한을 넘어 프롬프트 앞에 붙입니다."
                    )
                    prompt_str = f"System Instructions:\n{system_text}\n\nUser Input:\n{prompt_str}"
                else:
                    cmd = with_flag

            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt_str)

            logger.debug(f"Grok CLI 실행 시작 (모델: {self.model_name or 'default'}, 프롬프트 {len(prompt_str)}자)")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=build_isolated_env(),
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )

            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            try:
                data = json.loads(stdout_text) if stdout_text else None
            except json.JSONDecodeError:
                data = None

            if proc.returncode != 0 or (isinstance(data, dict) and data.get("type") == "error"):
                # 실패 시 stdout에 {"type": "error", "message": ...}를 내고 0이 아닌 코드로 끝난다
                err_msg = (data.get("message") if isinstance(data, dict) else None) or stderr_text or stdout_text
                logger.error(f"Grok CLI 비정상 종료 (code: {proc.returncode}): {err_msg}")
                if is_content_safety_error(err_msg):
                    raise BtgApiContentSafetyException(f"Grok CLI 콘텐츠 안전 차단: {err_msg}")
                if _is_rate_limited(err_msg):
                    raise BtgApiRateLimitException(f"Grok CLI 사용량 제한: {err_msg}")
                if _is_auth_error(err_msg):
                    raise BtgApiClientException(
                        f"Grok CLI 인증 필요. 터미널에서 `grok login`을 실행하세요: {err_msg}"
                    )
                raise BtgApiClientException(f"Grok CLI 실행 실패 (code {proc.returncode}): {err_msg}")

            if not isinstance(data, dict):
                return stdout_text

            text = str(data.get("text") or "").strip()
            if data.get("stopReason") == "refusal":
                raise BtgApiContentSafetyException(f"Grok CLI 콘텐츠 안전 거부: {text}")
            structured = data.get("structuredOutput")
            if schema_dict and structured is not None:
                return json.dumps(structured, ensure_ascii=False)
            return text

        except asyncio.TimeoutError as e:
            logger.error(f"Grok CLI 실행 시간 초과 ({self.timeout_seconds}초)")
            raise BtgApiClientException(
                f"Grok CLI 실행 시간 초과 ({self.timeout_seconds}초)", original_exception=e
            ) from e
        except FileNotFoundError as e:
            raise BtgApiClientException(
                f"Grok CLI 실행 파일을 찾을 수 없습니다: {self.cli_path}", original_exception=e
            ) from e
        finally:
            # 취소(바깥 wait_for, 번역 중지)·시간 초과 모두 여기서 프로세스를 끝낸 뒤 임시 폴더를 지운다.
            # TemporaryDirectory는 Python 3.10에서 잠긴 폴더를 만나면 무한 재귀에 빠지므로 쓰지 않는다.
            kill_if_running(proc)
            if tmp_root:
                shutil.rmtree(tmp_root, ignore_errors=True)

    async def list_models_async(self) -> List[str]:
        """`grok models`로 계정에서 쓸 수 있는 모델 목록을 반환한다."""
        if not self._is_available():
            return list(self.FALLBACK_MODELS)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path,
                "models",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=build_isolated_env(),
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode == 0:
                models = parse_model_list(stdout_data.decode("utf-8", errors="replace"))
                if models:
                    return ["default"] + models
        except asyncio.CancelledError:
            kill_if_running(proc)
            raise
        except Exception as e:
            kill_if_running(proc)
            logger.warning(f"grok models 조회 실패, 폴백 사용: {e!r}")

        return list(self.FALLBACK_MODELS)

    async def check_health_async(self) -> tuple[bool, str]:
        """
        Grok Build CLI 인증 및 실행 가능 상태를 점검합니다.
        """
        if not self._is_available():
            return (
                False,
                f"Grok CLI 실행 파일을 찾을 수 없습니다: '{self.cli_path}'.\n"
                f"Grok Build(grok)가 설치되어 있는지 확인하거나, 설정에서 실행 파일의 전체 경로를 지정해 주세요.",
            )

        try:
            response = await asyncio.wait_for(
                self.generate_text_async("Say 'OK' in one word."),
                timeout=60,
            )
            clean_resp = response.replace("\n", " ").strip()
            return True, f"Grok CLI 인증 성공 (grok.com 세션 정상 활성화됨. 응답: {clean_resp[:30]})"
        except asyncio.TimeoutError:
            return False, "Grok CLI 연결 시간 초과 (60초 내 응답 없음)."
        except BtgApiRateLimitException as e:
            return False, f"Grok CLI 사용량 제한: {e}"
        except BtgApiClientException as e:
            msg = str(e)
            if _is_auth_error(msg):
                return (
                    False,
                    f"Grok CLI 로그인이 필요합니다.\n"
                    f"터미널(PowerShell/CMD)을 열고 'grok login'을 실행해 로그인을 완료해 주세요.\n"
                    f"(세부 오류: {msg})",
                )
            return False, f"Grok CLI 인증/실행 실패: {msg}"
        except Exception as e:
            return False, f"Grok CLI 점검 중 예외 발생: {e}"
