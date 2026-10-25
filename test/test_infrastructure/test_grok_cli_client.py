"""
Unit tests for GrokCliClient adapter.
"""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiContentSafetyException,
)
from infrastructure import grok_cli_client
from infrastructure.grok_cli_client import (
    ALLOWED_TOOLS,
    COMPAT_ENV_VARS,
    GrokCliClient,
    build_isolated_env,
    parse_model_list,
)

# 2026-10-25 실제 `grok models` 출력
MODELS_SAMPLE = (
    "You are logged in with grok.com.\n"
    "\n"
    "Default model: grok-4.7\n"
    "\n"
    "Available models:\n"
    "  * grok-4.7 (default)\n"
    "  - grok-4.7-build-fast\n"
    "  - grok-4.6\n"
    "  - grok-4.5\n"
)


def _proc(stdout=b"", returncode=0, stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _result(**fields):
    data = {"type": "result", "text": "", "stopReason": "end_turn", "num_turns": 1}
    data.update(fields)
    return _proc(json.dumps(data, ensure_ascii=False).encode("utf-8"))


class TestGrokCliClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = GrokCliClient(cli_path="grok", model_name="grok-4.6", effort="low")

    def test_initialization_properties(self):
        self.assertEqual(self.client.provider_name, "grok_cli")
        self.assertFalse(self.client.supports_pagefold)
        self.assertEqual(self.client.model_name, "grok-4.6")
        self.assertEqual(self.client.effort, "low")
        self.assertIsNone(GrokCliClient(model_name="default").model_name)
        # grok-4.7은 none을 거부한다 (실측)
        self.assertIsNone(GrokCliClient(effort="none").effort)

    def test_falls_back_to_default_install_path(self):
        with patch.object(grok_cli_client.shutil, "which", return_value=None), \
             patch.object(grok_cli_client.os.path, "exists", return_value=True), \
             patch.dict(os.environ, {"GROK_HOME": os.path.join("C:\\", "fake", ".grok")}):
            client = GrokCliClient(cli_path="grok")
        self.assertTrue(client.cli_path.endswith(os.path.join("fake", ".grok", "bin", "grok.exe" if os.name == "nt" else "grok")))

    async def test_command_is_isolated_and_prompt_goes_through_file(self):
        seen = {}

        async def fake_exec(*args, **kwargs):
            args = list(args)
            with open(args[args.index("--prompt-file") + 1], encoding="utf-8") as f:
                seen["prompt"] = f.read()
            seen["args"], seen["env"] = args, kwargs["env"]
            seen["stdin"] = kwargs["stdin"]
            return _result(text="번역된 텍스트")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await self.client.generate_text_async("원문\n둘째 줄 & | % ^ \"인용\"")
        self.assertEqual(result, "번역된 텍스트")

        args = seen["args"]
        self.assertIn("--verbatim", args)
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--tools") + 1], ALLOWED_TOOLS)
        for flag in ("--no-subagents", "--disable-web-search", "--no-plan", "--no-auto-update"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("-m") + 1], "grok-4.6")
        self.assertEqual(args[args.index("--effort") + 1], "low")
        self.assertNotIn("--system-prompt-override", args)
        self.assertNotIn("--json-schema", args)
        # 프롬프트는 인자가 아니라 파일로 간다 (명령줄 길이 제한 회피)
        self.assertEqual(seen["prompt"], "원문\n둘째 줄 & | % ^ \"인용\"")
        self.assertFalse(any("원문" in a for a in args))
        self.assertEqual(seen["stdin"], asyncio.subprocess.DEVNULL)

        tmp_root = os.path.dirname(args[args.index("--cwd") + 1])
        self.assertTrue(os.path.basename(tmp_root).startswith("btg_grok_"))
        self.assertFalse(os.path.exists(tmp_root))

        env = seen["env"]
        for name in COMPAT_ENV_VARS:
            self.assertEqual(env[name], "false")
        self.assertEqual(env["GROK_HOME"], grok_cli_client.APP_GROK_HOME)
        self.assertTrue(env["GROK_AUTH_PATH"].endswith("auth.json"))

    def test_isolated_env_covers_hooks_and_writes_ttl_config(self):
        self.assertIn("GROK_CLAUDE_HOOKS_ENABLED", COMPAT_ENV_VARS)
        self.assertIn("GROK_CLAUDE_RULES_ENABLED", COMPAT_ENV_VARS)
        with patch.dict(os.environ, {"GROK_HOME": os.path.join("D:\\", "user", ".grok")}):
            env = build_isolated_env()
        # 사용자 GROK_HOME을 덮어써도 인증은 사용자 홈의 auth.json을 본다
        self.assertEqual(env["GROK_AUTH_PATH"], os.path.join("D:\\", "user", ".grok", "auth.json"))
        with open(os.path.join(grok_cli_client.APP_GROK_HOME, "config.toml"), encoding="utf-8") as f:
            self.assertIn("cleanup_ttl_days", f.read())

    async def test_long_system_instruction_moves_into_prompt_file(self):
        seen = {}

        async def fake_exec(*args, **kwargs):
            args = list(args)
            with open(args[args.index("--prompt-file") + 1], encoding="utf-8") as f:
                seen["prompt"] = f.read()
            seen["args"] = args
            return _result(text="OK")

        long_system = "기억 " * 20000
        with patch.object(grok_cli_client.os, "name", "nt"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await self.client.generate_text_async("원문", system_instruction_text=long_system)
        self.assertNotIn("--system-prompt-override", seen["args"])
        self.assertTrue(seen["prompt"].startswith("System Instructions:\n기억"))
        self.assertTrue(seen["prompt"].endswith("User Input:\n원문"))

    async def test_structured_output_is_returned_as_json_text(self):
        proc = _result(text="ignored", structuredOutput=[{"id": 1, "translated_text": "번역"}])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            out = await self.client.generate_text_async(
                "원문", generation_config_dict={"response_schema": {"type": "array", "items": {"type": "object"}}})
        self.assertEqual(json.loads(out), [{"id": 1, "translated_text": "번역"}])

    async def test_refusal_stop_reason_is_content_safety(self):
        proc = _result(text="I can't help with that.", stopReason="refusal")
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with self.assertRaises(BtgApiContentSafetyException):
                await self.client.generate_text_async("원문")

    async def test_error_object_classification(self):
        cases = [
            ("Blocked by safety policy", BtgApiContentSafetyException),
            ("rate limit exceeded, try again later", BtgApiRateLimitException),
            ("Not logged in. Run grok login", BtgApiClientException),
            ("Couldn't start session: boom", BtgApiClientException),
        ]
        for message, exc in cases:
            proc = _proc(json.dumps({"type": "error", "message": message}).encode(), returncode=1)
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
                with self.assertRaises(exc) as ctx:
                    await self.client.generate_text_async("원문")
            if exc is BtgApiClientException and "logged" in message:
                self.assertIn("grok login", str(ctx.exception))
            if exc is not BtgApiRateLimitException:
                self.assertNotIsInstance(ctx.exception, BtgApiRateLimitException)

    async def test_timeout_kills_process(self):
        proc = _proc()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with self.assertRaises(BtgApiClientException) as ctx:
                await self.client.generate_text_async("원문")
        self.assertIn("시간 초과", str(ctx.exception))
        proc.kill.assert_called_once()

    @patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("missing"))
    async def test_file_not_found(self, _mock_exec):
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("원문")
        self.assertIn("실행 파일을 찾을 수 없습니다", str(ctx.exception))

    async def test_list_models_parsed_with_isolated_env(self):
        proc = _proc(MODELS_SAMPLE.encode("utf-8"))
        with patch.object(GrokCliClient, "_is_available", return_value=True), \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
            models = await self.client.list_models_async()
        self.assertEqual(models, ["default", "grok-4.7", "grok-4.7-build-fast", "grok-4.6", "grok-4.5"])
        self.assertEqual(mock_exec.call_args.kwargs["env"]["GROK_HOME"], grok_cli_client.APP_GROK_HOME)

    async def test_list_models_falls_back_on_failure(self):
        with patch.object(GrokCliClient, "_is_available", return_value=True), \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc(returncode=1))):
            self.assertEqual(await self.client.list_models_async(), GrokCliClient.FALLBACK_MODELS)

    @patch.object(GrokCliClient, "generate_text_async", new_callable=AsyncMock)
    async def test_check_health_success(self, mock_gen):
        mock_gen.return_value = "OK"
        with patch.object(GrokCliClient, "_is_available", return_value=True):
            ok, msg = await self.client.check_health_async()
        self.assertTrue(ok)
        self.assertIn("인증 성공", msg)


class TestParseModelList(unittest.TestCase):
    def test_ids_only(self):
        self.assertEqual(parse_model_list(MODELS_SAMPLE), ["grok-4.7", "grok-4.7-build-fast", "grok-4.6", "grok-4.5"])

    def test_empty(self):
        self.assertEqual(parse_model_list("You are logged in with grok.com.\n"), [])


if __name__ == "__main__":
    unittest.main()
