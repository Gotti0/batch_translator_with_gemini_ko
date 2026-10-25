"""
CLI 클라이언트가 도메인 서비스의 GeminiClient식 호출 인자를 받는지 검증한다.

도메인 서비스는 지침을 `system_instruction_text`, 스키마를 `generation_config_dict["response_schema"]`로
넘긴다. 예전에는 CLI 클라이언트 4개가 이 이름을 보지 않아 **kwargs로 버렸다.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from core.exceptions import BtgApiContentSafetyException
from infrastructure import cursor_cli_client
from infrastructure.antigravity_cli_client import AntigravityCliClient
from infrastructure.base_client import (
    resolve_call_options,
    schema_to_dict,
    unwrap_array_result,
    wrap_array_schema,
)
from infrastructure.claude_cli_client import ClaudeCliClient
from infrastructure.codex_cli_client import CodexCliClient, to_strict_schema
from infrastructure.cursor_cli_client import CursorCliClient


class Unit(BaseModel):
    id: int
    text: str = ""


SYSTEM = "프리필 지침과 번역 기억"
DOMAIN_KWARGS = {
    "system_instruction_text": SYSTEM,
    "generation_config_dict": {"response_schema": list[Unit], "response_mime_type": "application/json"},
}


def _proc(stdout=b"", returncode=0, stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


# --- 공통 헬퍼 ---

def test_resolve_call_options_reads_domain_names():
    assert resolve_call_options(None, None, dict(DOMAIN_KWARGS)) == (SYSTEM, list[Unit])
    # 직접 넘긴 값이 우선한다
    assert resolve_call_options("direct", dict, dict(DOMAIN_KWARGS)) == ("direct", dict)
    assert resolve_call_options(None, None, {}) == (None, None)


def test_schema_to_dict_handles_list_of_models():
    schema = schema_to_dict(list[Unit])
    assert schema["type"] == "array"
    assert schema["items"] == {"$ref": "#/$defs/Unit"}


def test_wrap_array_schema_hoists_defs_and_unwraps():
    wrapped, did_wrap = wrap_array_schema(schema_to_dict(list[Unit]))
    assert did_wrap
    assert wrapped["type"] == "object" and wrapped["required"] == ["items"]
    assert "Unit" in wrapped["$defs"]
    assert "$defs" not in wrapped["properties"]["items"]
    assert unwrap_array_result({"items": [{"id": 1}]}) == [{"id": 1}]

    obj = {"type": "object", "properties": {}}
    assert wrap_array_schema(obj) == (obj, False)


def test_to_strict_schema_requires_all_properties():
    wrapped, _ = wrap_array_schema(schema_to_dict(list[Unit]))
    strict = to_strict_schema(wrapped)
    unit = strict["$defs"]["Unit"]
    assert unit["additionalProperties"] is False
    assert unit["required"] == ["id", "text"]
    assert "default" not in unit["properties"]["text"]
    assert strict["additionalProperties"] is False


# --- 클라이언트별 ---

def test_claude_uses_system_prompt_and_json_schema():
    structured = {"items": [{"id": 1, "text": "번역"}]}
    proc = _proc(json.dumps({"is_error": False, "result": "", "structured_output": structured}).encode())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
        out = asyncio.run(ClaudeCliClient(cli_path="claude").generate_text_async("원문", **DOMAIN_KWARGS))

    args = list(mock_exec.call_args[0])
    assert args[args.index("--system-prompt") + 1] == SYSTEM
    schema = json.loads(args[args.index("--json-schema") + 1])
    assert schema["type"] == "object" and "items" in schema["properties"]
    assert json.loads(out) == [{"id": 1, "text": "번역"}]


def test_codex_sends_strict_wrapped_schema_and_unwraps():
    seen = {}

    async def fake_exec(*args, **kwargs):
        path = args[list(args).index("--output-schema") + 1]
        with open(path, encoding="utf-8") as f:
            seen["schema"] = json.load(f)
        event = {"item": {"type": "agent_message", "text": json.dumps({"items": [{"id": 1, "text": "번역"}]})}}
        return _proc(json.dumps(event).encode())

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        out = asyncio.run(CodexCliClient(cli_path="codex").generate_text_async("원문", **DOMAIN_KWARGS))

    assert seen["schema"]["additionalProperties"] is False
    assert seen["schema"]["$defs"]["Unit"]["required"] == ["id", "text"]
    assert json.loads(out) == [{"id": 1, "text": "번역"}]


def test_codex_prompt_includes_system_instruction():
    proc = _proc(json.dumps({"item": {"type": "agent_message", "text": "OK"}}).encode())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(CodexCliClient(cli_path="codex").generate_text_async("원문", system_instruction_text=SYSTEM))
    sent = proc.communicate.call_args.kwargs["input"].decode("utf-8")
    assert sent.startswith(f"System Instructions:\n{SYSTEM}")


def test_antigravity_prompt_includes_system_instruction():
    seen = {}

    async def fake_exec(*args, **kwargs):
        prompt_arg = args[list(args).index("-p") + 1]
        with open(prompt_arg[1:], encoding="utf-8") as f:
            seen["prompt"] = f.read()
        return _proc(json.dumps({"status": "SUCCESS", "response": "OK"}).encode())

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.run(AntigravityCliClient(cli_path="agy").generate_text_async("원문", system_instruction_text=SYSTEM))
    assert seen["prompt"].startswith(f"System Instructions:\n{SYSTEM}")


@pytest.fixture
def cursor_client():
    with patch.object(cursor_cli_client, "resolve_cursor_command", return_value=["cursor-agent"]):
        yield CursorCliClient(cli_path="cursor-agent")


def test_cursor_prompt_includes_system_and_schema(cursor_client):
    proc = _proc(json.dumps({"is_error": False, "result": "[]"}).encode())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
        asyncio.run(cursor_client.generate_text_async("원문", **DOMAIN_KWARGS))
    prompt = mock_exec.call_args[0][-1]
    assert prompt.startswith(f"System Instructions:\n{SYSTEM}")
    # 예전에는 list[Unit]을 str()로 넣어 스키마 대신 "list[...]"가 들어갔다
    assert '"$defs"' in prompt and "list[" not in prompt


def test_cursor_provider_block_is_content_safety(cursor_client):
    # 2026-10-25 실측 문구
    proc = _proc(returncode=1, stderr=(
        b"ActionRequiredError: Request blocked We are unable to complete this request because it was blocked "
        b"under the model provider's usage guidelines. Try a less sensitive prompt."))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(BtgApiContentSafetyException):
            asyncio.run(cursor_client.generate_text_async("원문"))


def test_cursor_cancel_kills_before_removing_temp_dir(cursor_client):
    """취소 시 CancelledError가 그대로 올라오고, 프로세스를 끝낸 뒤 임시 폴더를 지운다."""
    order = []
    proc = MagicMock()
    proc.returncode = None

    async def hang(*_a, **_k):
        await asyncio.Event().wait()

    proc.communicate = hang
    proc.kill = MagicMock(side_effect=lambda: order.append("kill"))
    real_rmtree = cursor_cli_client.shutil.rmtree

    def tracking_rmtree(path, ignore_errors=False):
        order.append("rmtree")
        real_rmtree(path, ignore_errors=ignore_errors)

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(cursor_client.generate_text_async("원문"), timeout=0.05)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec, \
         patch.object(cursor_cli_client.shutil, "rmtree", side_effect=tracking_rmtree):
        asyncio.run(run())

    assert order == ["kill", "rmtree"]
    workspace = mock_exec.call_args[0][list(mock_exec.call_args[0]).index("--workspace") + 1]
    assert not os.path.exists(os.path.dirname(workspace))
