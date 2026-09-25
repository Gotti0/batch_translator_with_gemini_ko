"""
프로바이더별 추론 강도 옵션이 실제 호출 인자로 전달되는지 검증한다.
값이 None(기본값)이면 옵션을 아예 넘기지 않아 기존 동작이 유지되어야 한다.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infrastructure.antigravity_cli_client import AntigravityCliClient
from infrastructure.claude_cli_client import ClaudeCliClient
from infrastructure.codex_cli_client import CodexCliClient
from infrastructure.llm_client_factory import LLMClientFactory
from infrastructure.ollama_client import OllamaClient
from infrastructure.OpenAICompatibleClient import OpenAICompatibleClient
from infrastructure.reasoning_options import (
    CLAUDE_CLI,
    GEMINI_3_FLASH,
    GEMINI_3_PRO,
    GEMINI_BUDGET,
    NO_REASONING,
    reasoning_spec_for,
)


def _run_cli(client, stdout=b'{"result": "OK"}'):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as exec_mock:
        asyncio.run(client.generate_text_async("hi"))
    return list(exec_mock.call_args[0])


# --- 명세 ---

def test_spec_lookup():
    assert reasoning_spec_for("claude_cli") is CLAUDE_CLI
    assert reasoning_spec_for("gemini", "gemini-3-flash-preview") is GEMINI_3_FLASH
    assert reasoning_spec_for("gemini", "gemini-3-pro-preview") is GEMINI_3_PRO
    assert reasoning_spec_for("gemini", "gemini-2.5-pro") is GEMINI_BUDGET
    assert reasoning_spec_for("gemini", "gemini-2.0-flash") is GEMINI_BUDGET  # 기존 UI와 같은 기준
    assert reasoning_spec_for("unknown") is NO_REASONING


def test_normalize_rejects_unknown_values():
    assert CLAUDE_CLI.normalize("HIGH") == "high"
    assert CLAUDE_CLI.normalize("bogus") is None
    assert CLAUDE_CLI.normalize(None) is None
    # Gemini 3는 "기본값" 항목이 없고 high로 돌아간다 (기존 동작)
    assert GEMINI_3_PRO.normalize("medium") == "high"


# --- CLI 인자 ---

def test_claude_effort_flag():
    assert "--effort" not in _run_cli(ClaudeCliClient(cli_path="claude"))
    args = _run_cli(ClaudeCliClient(cli_path="claude", effort="xhigh"))
    assert args[args.index("--effort") + 1] == "xhigh"


def test_codex_effort_config_override():
    assert not any("model_reasoning_effort" in a for a in _run_cli(CodexCliClient(cli_path="codex"), stdout=b""))
    args = _run_cli(CodexCliClient(cli_path="codex", effort="max"), stdout=b"")
    assert args[args.index("-c") + 1] == 'model_reasoning_effort="max"'


def test_antigravity_accepts_max():
    """예전에는 low/medium/high만 받아 max를 조용히 버렸다"""
    client = AntigravityCliClient(cli_path="agy", effort="max")
    assert client.effort == "max"
    args = _run_cli(client, stdout=b'{"status": "SUCCESS", "response": "OK"}')
    assert args[args.index("--effort") + 1] == "max"


# --- API 요청 본문 ---

@pytest.mark.parametrize("think, expected", [(None, "absent"), ("false", False), ("true", True), ("high", "high")])
def test_ollama_think_payload(think, expected):
    client = OllamaClient(model_name="qwen3:14b", think=think)
    with patch.object(client, "_request_async", AsyncMock(return_value={"message": {"content": "OK"}})) as req:
        asyncio.run(client.generate_text_async("hi"))
    payload = req.call_args[0][2]
    if expected == "absent":
        assert "think" not in payload
    else:
        assert payload["think"] == expected


@pytest.mark.parametrize("effort", [None, "low"])
def test_openai_compatible_reasoning_effort(effort):
    client = OpenAICompatibleClient(api_key="k", base_url="http://x/v1/chat/completions",
                                    default_model="m", reasoning_effort=effort)
    with patch.object(client, "generate_text", return_value="OK") as gen:
        asyncio.run(client.generate_text_async("hi"))
    gen_config = gen.call_args.kwargs["generation_config"]
    if effort is None:
        assert "reasoning_effort" not in gen_config
    else:
        assert gen_config["reasoning_effort"] == "low"


# --- 팩토리 ---

def test_factory_passes_provider_effort():
    assert LLMClientFactory.create_client({"llm_provider": "claude_cli", "claude_cli_effort": "high"}).effort == "high"
    assert LLMClientFactory.create_client({"llm_provider": "codex_cli", "codex_cli_effort": "minimal"}).effort == "minimal"
    assert LLMClientFactory.create_client({"llm_provider": "antigravity_cli", "antigravity_cli_effort": "max"}).effort == "max"
    assert LLMClientFactory.create_client({"llm_provider": "ollama", "ollama_think": "false"}).think == "false"
    client = LLMClientFactory.create_client({
        "llm_provider": "openai_compatible", "openai_compatible_base_url": "http://x/v1/chat/completions",
        "openai_compatible_api_key": "k", "openai_compatible_reasoning_effort": "medium",
    })
    assert client.reasoning_effort == "medium"
    # 다른 프로바이더의 값은 섞이지 않는다
    assert LLMClientFactory.create_client({"llm_provider": "claude_cli", "codex_cli_effort": "max"}).effort is None
