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
import re
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
    def _extract_json_data(text: str) -> Any:
        """
        모델 응답에서 JSON 데이터(배열 또는 객체)를 견고하게 추출합니다.
        중복 출력, 코드 블록, 설명 텍스트가 섞여 있어도 첫 번째 완전한 JSON을 디코딩합니다.
        """
        t = text.strip()
        # 1. 마크다운 코드 블록 우선 검사
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
        candidates = [fence_match.group(1).strip()] if fence_match else []
        candidates.append(t)

        decoder = json.JSONDecoder()
        for cand in candidates:
            try:
                return json.loads(cand)
            except (json.JSONDecodeError, ValueError):
                pass
            # raw_decode로 첫 번째 유효 JSON 구조체 탐색
            for idx in range(len(cand)):
                if cand[idx] in ('[', '{'):
                    try:
                        obj, _ = decoder.raw_decode(cand[idx:])
                        return obj
                    except (json.JSONDecodeError, ValueError):
                        continue
        raise ValueError("유효한 JSON 구조를 찾을 수 없습니다.")

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
        `agy -p @<temp_prompt> --output-format json` 비동기 서브프로세스를 호출하여 텍스트를 생성합니다.
        """
        prompt_str = self._prepare_prompt_string(prompt, system_instruction)

        prompt_temp_path = None
        schema_temp_path = None
        try:
            cmd = [
                self.cli_path,
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--disable-slash-commands",
            ]

            if self.model_name:
                cmd.extend(["--model", self.model_name])

            if self.effort:
                cmd.extend(["--effort", self.effort])

            gen_config: Dict[str, Any] = dict(kwargs.get("generation_config_dict") or {})
            if response_schema is None:
                response_schema = gen_config.get("response_schema")
            wants_json = response_schema is not None or gen_config.get("response_mime_type") == "application/json"

            if response_schema is not None:
                schema_dict = None
                if isinstance(response_schema, dict):
                    schema_dict = response_schema
                elif hasattr(response_schema, "model_json_schema"):
                    schema_dict = response_schema.model_json_schema()
                else:
                    try:
                        from pydantic import TypeAdapter

                        schema_dict = TypeAdapter(response_schema).json_schema()
                    except Exception as e_adapt:
                        logger.warning(f"AGY Pydantic TypeAdapter 스키마 변환 실패: {e_adapt}")

                if schema_dict:
                    # agy CLI의 --json-schema는 최상위 타입이 반드시 object여야 함 (array일 경우 Gemini API 400 에러 발생).
                    # array 스키마인 경우 {"type": "object", "properties": {"items": ...}, "required": ["items"]}로 래핑
                    if schema_dict.get("type") == "array":
                        defs = schema_dict.pop("$defs", None) or schema_dict.pop("definitions", None)
                        schema_dict = {
                            "type": "object",
                            "properties": {
                                "items": schema_dict,
                            },
                            "required": ["items"],
                        }
                        if defs:
                            schema_dict["$defs"] = defs
                        prompt_str += "\n\nCRITICAL: You MUST output the results inside the 'items' array of the JSON response."

                    try:
                        with tempfile.NamedTemporaryFile(
                            "w", suffix=".json", delete=False, encoding="utf-8"
                        ) as f:
                            json.dump(schema_dict, f, ensure_ascii=False)
                            schema_temp_path = f.name
                        cmd.extend(["--json-schema", schema_temp_path])
                    except Exception as e_schema:
                        logger.warning(f"AGY JSON Schema 임시 파일 생성 실패: {e_schema}")

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as pf:
                pf.write(prompt_str)
                prompt_temp_path = pf.name

            cmd.extend(["-p", f"@{prompt_temp_path}"])

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            logger.debug(f"Antigravity CLI 실행 시작: {' '.join(cmd[:4])} ...")

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

            # JSON 출력 파싱 시도 (AGY 래퍼 형식: {"status": "SUCCESS", "response": "...", "structured_output": ...})
            resp_content = stdout_text
            agy_structured_output = None
            try:
                data = json.loads(stdout_text)
                if isinstance(data, dict):
                    if data.get("status") == "ERROR":
                        raise BtgApiClientException(
                            f"Antigravity CLI 응답 오류: {data.get('response') or data.get('error') or stdout_text}"
                        )
                    agy_structured_output = data.get("structured_output")
                    resp = data.get("response")
                    if resp is not None:
                        resp_content = str(resp).strip()
            except json.JSONDecodeError:
                pass

            if not wants_json:
                return resp_content

            # 구조화 출력 모드: agy가 직접 반환한 structured_output 우선 사용 또는 지능형 JSON 추출
            try:
                parsed = None
                if agy_structured_output is not None:
                    # agy_structured_output이 유효한 비어있지 않은 데이터인 경우 우선 채택
                    if isinstance(agy_structured_output, dict):
                        for k in ("items", "units", "terms"):
                            if k in agy_structured_output and isinstance(agy_structured_output[k], list) and agy_structured_output[k]:
                                parsed = agy_structured_output[k]
                                break
                        if parsed is None and any(agy_structured_output.values()):
                            parsed = agy_structured_output
                    elif isinstance(agy_structured_output, list) and agy_structured_output:
                        parsed = agy_structured_output

                if parsed is None:
                    try:
                        parsed = self._extract_json_data(resp_content)
                    except Exception:
                        if agy_structured_output is not None:
                            parsed = agy_structured_output
                        else:
                            raise

                if isinstance(parsed, dict):
                    if "units" in parsed and isinstance(parsed["units"], list):
                        parsed = parsed["units"]
                    elif "items" in parsed and isinstance(parsed["items"], list):
                        parsed = parsed["items"]
                    elif "terms" in parsed and isinstance(parsed["terms"], list):
                        parsed = parsed["terms"]

                if response_schema is not None and not isinstance(response_schema, dict):
                    try:
                        from pydantic import TypeAdapter

                        return TypeAdapter(response_schema).validate_python(parsed)
                    except Exception as e_validate:
                        logger.warning(f"AGY 응답 Pydantic 스키마 검증 실패, 파싱된 JSON 반환: {e_validate}")
                return parsed
            except Exception as e_parse:
                logger.warning(f"AGY JSON 파싱 실패 ({e_parse}), 원문 반환: {resp_content[:200]}")
                return resp_content

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
            for p in (prompt_temp_path, schema_temp_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
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
