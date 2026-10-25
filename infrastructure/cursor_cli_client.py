"""
Cursor Agent CLI Client Adapter for Neo Batch Translator (BTG)

사용자의 로컬 시스템에 설치된 Cursor Agent CLI(cursor-agent)를
비동기 서브프로세스로 실행하여, 별도 API 키 없이 로그인된 Cursor 세션으로
번역을 수행하는 런타임 어댑터입니다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, List, Optional, Union

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiInvalidRequestException,
    BtgApiContentSafetyException,
)
from infrastructure.base_client import BaseLLMClient, kill_if_running, is_content_safety_error
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

# Windows CreateProcess 명령줄 최대 길이(널 문자 제외)
WINDOWS_MAX_COMMAND_LINE = 32766

# 런처 ps1이 버전 폴더를 고르는 규칙과 같다 (YYYY.MM.DD-commit 또는 YYYY.MM.DD-HH-MM-SS-commit)
_VERSION_DIR_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[a-f0-9]+$")


def _is_rate_limited(err_msg: str) -> bool:
    """사용량 제한·할당량 소진 오류인지 판별한다."""
    lowered = err_msg.lower()
    return any(
        k in lowered
        for k in ("rate limit", "too many requests", "usage limit", "resource_exhausted", "quota exceeded")
    )


def _is_auth_error(err_msg: str) -> bool:
    lowered = err_msg.lower()
    return "login" in lowered or "auth" in lowered


def _version_sort_key(name: str) -> tuple:
    m = _VERSION_DIR_RE.match(name)
    return tuple(int(g or 0) for g in m.groups()) if m else ()


def resolve_cursor_command(cli_path: str) -> List[str]:
    """cursor-agent를 실행할 명령 앞부분을 돌려준다.

    Windows 런처(.cmd/.ps1)는 cmd.exe와 PowerShell을 거쳐 node로 넘기므로, 여러 줄이나 특수문자가 든
    프롬프트 인자가 깨진다. print 모드는 stdin을 읽지 않아 프롬프트를 인자로만 줄 수 있으므로
    런처를 건너뛰고 최신 버전 폴더의 node.exe와 index.js를 직접 실행한다.
    자동 업데이트로 버전 폴더가 바뀌므로 호출할 때마다 다시 찾는다.
    런처의 promote-staged 단계를 건너뛰므로, 받아 둔 업데이트는 터미널에서 cursor-agent를
    한 번 실행해야 적용된다.
    """
    resolved = shutil.which(cli_path) or cli_path
    if os.name != "nt" or os.path.splitext(resolved)[1].lower() not in (".cmd", ".ps1", ".bat"):
        return [resolved]

    launcher_dir = os.path.dirname(os.path.abspath(resolved))
    candidates = [launcher_dir]
    versions_dir = os.path.join(launcher_dir, "versions")
    if os.path.isdir(versions_dir):
        names = sorted(
            (n for n in os.listdir(versions_dir) if _VERSION_DIR_RE.match(n)),
            key=_version_sort_key,
            reverse=True,
        )
        candidates.extend(os.path.join(versions_dir, n) for n in names)

    for d in candidates:
        node = os.path.join(d, "node.exe")
        index_js = os.path.join(d, "index.js")
        if os.path.isfile(node) and os.path.isfile(index_js):
            return [node, index_js]

    raise BtgApiClientException(
        f"Cursor Agent CLI 런처({resolved}) 옆에서 node.exe와 index.js가 든 버전 폴더를 찾지 못했습니다. "
        f"터미널에서 `cursor-agent update`를 실행하거나 재설치해 주세요."
    )


def parse_model_list(output: str) -> List[str]:
    """`cursor-agent --list-models` 출력(`id - Label` 줄)에서 모델 ID만 뽑는다."""
    models: List[str] = []
    for line in output.splitlines():
        line = line.replace("​", "").strip()
        if not line or " - " not in line or line.startswith("Tip:"):
            continue
        model_id = line.split(" - ", 1)[0].strip()
        if model_id and " " not in model_id and model_id not in models:
            models.append(model_id)
    if "auto" in models:
        models.remove("auto")
    return ["auto"] + models if models else []


class CursorCliClient(BaseLLMClient):
    """
    Cursor Agent CLI (`cursor-agent -p`) 서브프로세스 래퍼 클라이언트.
    """

    FALLBACK_MODELS = [
        "auto",
        "gpt-5.6-sol-medium",
        "claude-sonnet-5-5-medium",
        "claude-opus-5-5-medium",
        "gemini-3.8-flash-medium",
        "composer-2.5",
    ]

    def __init__(
        self,
        cli_path: str = "cursor-agent",
        model_name: Optional[str] = None,
        timeout_seconds: int = 180,
    ) -> None:
        """
        CursorCliClient를 초기화합니다.

        Args:
            cli_path: cursor-agent 실행 파일 경로 또는 커맨드 이름
            model_name: 사용할 모델 ID (None 또는 'auto'/'default'이면 Cursor 기본값). 추론 강도는 모델 ID에 들어 있다.
            timeout_seconds: 서브프로세스 실행 타임아웃(초)
        """
        resolved_path = shutil.which(cli_path)
        self.cli_path = resolved_path if resolved_path else cli_path
        self.model_name = model_name if model_name and model_name not in ("auto", "default") else None
        self.timeout_seconds = timeout_seconds

        logger.info(
            f"CursorCliClient 초기화 완료 (실행 경로: {self.cli_path}, 모델: {self.model_name or 'Cursor 기본값(auto)'})"
        )

    @property
    def provider_name(self) -> str:
        return "cursor_cli"

    @property
    def supports_pagefold(self) -> bool:
        return False

    def _prepare_prompt_string(
        self, prompt: Union[str, Any], system_instruction: Optional[str] = None
    ) -> str:
        """프롬프트와 시스템 지침을 결합하여 문자열을 준비합니다."""
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

        # cursor-agent에는 시스템 프롬프트 옵션이 없다
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
        `cursor-agent -p --mode ask` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        prompt_str = self._prepare_prompt_string(prompt, system_instruction)

        if response_schema is not None:
            if hasattr(response_schema, "model_json_schema"):
                schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
            elif isinstance(response_schema, dict):
                schema_json = json.dumps(response_schema, ensure_ascii=False)
            else:
                schema_json = str(response_schema)
            prompt_str += (
                f"\n\n[OUTPUT REQUIREMENT]: Respond with ONLY valid JSON strictly matching the following schema. "
                f"Do not include markdown code block formatting (e.g. ```json) or preamble:\n{schema_json}"
            )

        proc = None
        try:
            # 취소 시 프로세스가 아직 폴더를 잡고 있을 수 있다. 정리 실패가 CancelledError를 가리면 안 된다.
            with tempfile.TemporaryDirectory(prefix="btg_cursor_", ignore_cleanup_errors=True) as tmp_root:
                # ask 모드는 읽기 전용이라 셸·파일 쓰기를 막는다. 빈 임시 작업 폴더로 사용자 파일 접근도 막는다.
                workspace = os.path.join(tmp_root, "workspace")
                config_dir = os.path.join(tmp_root, "config")
                os.mkdir(workspace)
                os.mkdir(config_dir)
                cmd = resolve_cursor_command(self.cli_path) + [
                    "-p",
                    "--output-format",
                    "json",
                    "--mode",
                    "ask",
                    "--trust",
                    "--workspace",
                    workspace,
                ]
                if self.model_name:
                    cmd.extend(["--model", self.model_name])
                cmd.append(prompt_str)

                if os.name == "nt" and len(subprocess.list2cmdline(cmd)) > WINDOWS_MAX_COMMAND_LINE:
                    raise BtgApiInvalidRequestException(
                        f"Cursor Agent CLI 프롬프트가 Windows 명령줄 길이 상한({WINDOWS_MAX_COMMAND_LINE}자)을 넘습니다 "
                        f"(프롬프트 {len(prompt_str)}자). 청크 크기를 줄여 주세요."
                    )

                logger.debug(f"Cursor Agent CLI 실행 시작 (모델: {self.model_name or 'auto'}, 프롬프트 {len(prompt_str)}자)")

                # print 모드에서도 --model은 설정 폴더의 cli-config.json 기본 모델을 덮어쓰고, 대화 기록도
                # 그 폴더의 chats/에 쌓인다. 사용자의 ~/.cursor를 건드리지 않도록 호출마다 빈 폴더를 준다.
                # 로그인 정보는 이 폴더 밖에 있어 인증은 유지된다 (2026-10-25 실측).
                env = os.environ.copy()
                env["CURSOR_CONFIG_DIR"] = config_dir

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout_seconds,
                )

            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.error(
                    f"Cursor Agent CLI 비정상 종료 (code: {proc.returncode}): {stderr_text or stdout_text}"
                )
                err_msg = stderr_text or stdout_text
                if is_content_safety_error(err_msg):
                    raise BtgApiContentSafetyException(f"Cursor Agent CLI 콘텐츠 안전 차단: {err_msg}")
                if "actionrequirederror" in err_msg.lower():
                    # 요금제에 포함되지 않은 모델 등 사용자 조치가 필요한 상태라 기다려도 풀리지 않는다.
                    # 사용량 제한으로 분류하면 재시도만 반복하므로 일반 오류로 올린다.
                    raise BtgApiClientException(
                        f"Cursor Agent CLI 사용자 조치 필요 (다른 모델을 선택하거나 Cursor 요금제를 확인하세요): {err_msg}"
                    )
                if _is_rate_limited(err_msg):
                    raise BtgApiRateLimitException(f"Cursor Agent CLI 사용량 제한: {err_msg}")
                if _is_auth_error(err_msg):
                    raise BtgApiClientException(
                        f"Cursor Agent CLI 인증 필요. 터미널에서 `cursor-agent login`을 실행하세요: {err_msg}"
                    )
                raise BtgApiClientException(
                    f"Cursor Agent CLI 실행 실패 (code {proc.returncode}): {err_msg}"
                )

            # JSON 출력 형식: {"type": "result", "is_error": false, "result": "...", "usage": {...}}
            try:
                data = json.loads(stdout_text)
            except json.JSONDecodeError:
                return stdout_text
            if not isinstance(data, dict):
                return stdout_text
            if data.get("is_error"):
                detail = str(data.get("result") or stdout_text)
                if is_content_safety_error(detail):
                    raise BtgApiContentSafetyException(f"Cursor Agent CLI 콘텐츠 안전 차단: {detail}")
                if _is_rate_limited(detail):
                    raise BtgApiRateLimitException(f"Cursor Agent CLI 사용량 제한: {detail}")
                raise BtgApiClientException(f"Cursor Agent CLI 응답 오류: {detail}")
            return str(data.get("result") or "").strip()

        except asyncio.CancelledError:
            # 바깥 wait_for(헬스체크 제한 시간)나 번역 중지로 취소되면 안쪽 TimeoutError 경로를 타지 않는다.
            # 그대로 두면 CLI 프로세스가 살아남아 호출을 계속하므로 여기서 정리한다.
            kill_if_running(proc)
            raise
        except asyncio.TimeoutError as e:
            logger.error(f"Cursor Agent CLI 실행 시간 초과 ({self.timeout_seconds}초)")
            kill_if_running(proc)
            raise BtgApiClientException(
                f"Cursor Agent CLI 실행 시간 초과 ({self.timeout_seconds}초)", original_exception=e
            ) from e
        except FileNotFoundError as e:
            raise BtgApiClientException(
                f"Cursor Agent CLI 실행 파일을 찾을 수 없습니다: {self.cli_path}", original_exception=e
            ) from e

    async def list_models_async(self) -> List[str]:
        """`cursor-agent --list-models`로 계정에서 쓸 수 있는 모델 목록을 반환한다."""
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return list(self.FALLBACK_MODELS)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_cursor_command(self.cli_path),
                "--list-models",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                models = parse_model_list(stdout_data.decode("utf-8", errors="replace"))
                if models:
                    return models
        except asyncio.CancelledError:
            kill_if_running(proc)
            raise
        except Exception as e:
            kill_if_running(proc)
            logger.warning(f"cursor-agent --list-models 조회 실패, 폴백 사용: {e!r}")

        return list(self.FALLBACK_MODELS)

    async def check_health_async(self) -> tuple[bool, str]:
        """
        Cursor Agent CLI 인증 및 실행 가능 상태를 점검합니다.
        """
        if not shutil.which(self.cli_path) and not os.path.exists(self.cli_path):
            return (
                False,
                f"Cursor Agent CLI 실행 파일을 찾을 수 없습니다: '{self.cli_path}'.\n"
                f"Cursor Agent CLI(cursor-agent)가 설치되어 있는지 확인하거나, 설정에서 실행 파일의 전체 경로를 지정해 주세요.",
            )

        try:
            response = await asyncio.wait_for(
                self.generate_text_async("Say 'OK' in one word."),
                timeout=60,
            )
            clean_resp = response.replace("\n", " ").strip()
            return True, f"Cursor Agent CLI 인증 성공 (로그인 세션 정상 활성화됨. 응답: {clean_resp[:30]})"
        except asyncio.TimeoutError:
            return False, "Cursor Agent CLI 연결 시간 초과 (60초 내 응답 없음)."
        except BtgApiRateLimitException as e:
            return False, f"Cursor Agent CLI 사용량 제한: {e}"
        except BtgApiClientException as e:
            msg = str(e)
            if _is_auth_error(msg):
                return (
                    False,
                    f"Cursor Agent CLI 로그인이 필요합니다.\n"
                    f"터미널(PowerShell/CMD)을 열고 'cursor-agent login'을 실행해 로그인을 완료해 주세요.\n"
                    f"(세부 오류: {msg})",
                )
            return False, f"Cursor Agent CLI 인증/실행 실패: {msg}"
        except Exception as e:
            return False, f"Cursor Agent CLI 점검 중 예외 발생: {e}"
