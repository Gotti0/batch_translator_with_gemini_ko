"""
Unit tests for CursorCliClient adapter.
"""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import (
    BtgApiClientException,
    BtgApiRateLimitException,
    BtgApiInvalidRequestException,
    BtgApiContentSafetyException,
)
from infrastructure import cursor_cli_client
from infrastructure.cursor_cli_client import CursorCliClient, parse_model_list, resolve_cursor_command

# 2026-10-25 실제 `agent --list-models` 출력 일부. 라벨에 꼬리표와 U+200B가 섞여 있다.
LIST_MODELS_SAMPLE = (
    "Available models\n"
    "\n"
    "auto - Auto (current, default)\n"
    "gpt-5.3-codex-low - Codex 5.3 Low\n"
    "claude-fable-5-thinking-high - Claude Fable 5 1M Thinking (NO ZDR)\n"
    "grok-4.7-low-fast - Grok 4.7  Low Fast\u200b\u200b\n"
    "claude-opus-5-5-max - Claude Opus 5.5 1M Max\n"
    "\n"
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. Parameterized models also accept "
    "quoted overrides, e.g. --model 'claude-opus-4-8[context=1m,effort=high,fast=false]'.\n"
)


def _proc(returncode=0, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _result_json(result, is_error=False):
    return json.dumps(
        {"type": "result", "subtype": "success", "is_error": is_error, "result": result,
         "usage": {"inputTokens": 120, "outputTokens": 10}},
        ensure_ascii=False,
    ).encode("utf-8")


class TestCursorCliClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(cursor_cli_client, "resolve_cursor_command", return_value=["cursor-agent"])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = CursorCliClient(cli_path="cursor-agent", model_name="claude-sonnet-5-5-medium")

    def test_initialization_properties(self):
        self.assertEqual(self.client.provider_name, "cursor_cli")
        self.assertFalse(self.client.supports_pagefold)
        self.assertEqual(self.client.model_name, "claude-sonnet-5-5-medium")
        self.assertIsNone(CursorCliClient(model_name="auto").model_name)
        self.assertIsNone(CursorCliClient(model_name="default").model_name)

    @patch("asyncio.create_subprocess_exec")
    async def test_generate_builds_read_only_command(self, mock_exec):
        mock_exec.return_value = _proc(stdout=_result_json("번역된 텍스트"))

        result = await self.client.generate_text_async("원문\n둘째 줄", system_instruction="번역가")
        self.assertEqual(result, "번역된 텍스트")

        args = list(mock_exec.call_args[0])
        self.assertEqual(args[:2], ["cursor-agent", "-p"])
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertEqual(args[args.index("--mode") + 1], "ask")
        self.assertIn("--trust", args)
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5-medium")
        workspace = args[args.index("--workspace") + 1]
        tmp_root = os.path.dirname(workspace)
        self.assertTrue(os.path.basename(tmp_root).startswith("btg_cursor_"))
        self.assertFalse(os.path.exists(tmp_root))  # 호출 후 정리된다
        # 사용자 ~/.cursor의 기본 모델·대화 기록을 건드리지 않도록 설정 폴더를 격리한다
        config_dir = mock_exec.call_args.kwargs["env"]["CURSOR_CONFIG_DIR"]
        self.assertEqual(os.path.dirname(config_dir), tmp_root)
        self.assertNotEqual(config_dir, workspace)
        # 프롬프트는 마지막 인자로 줄바꿈째 넘어간다 (print 모드는 stdin을 읽지 않는다)
        self.assertEqual(args[-1], "System Instructions:\n번역가\n\nUser Input:\n원문\n둘째 줄")
        self.assertEqual(mock_exec.call_args.kwargs["stdin"], asyncio.subprocess.DEVNULL)

    @patch("asyncio.create_subprocess_exec")
    async def test_auto_model_omits_model_flag(self, mock_exec):
        mock_exec.return_value = _proc(stdout=_result_json("OK"))
        await CursorCliClient(model_name="auto").generate_text_async("hi")
        self.assertNotIn("--model", mock_exec.call_args[0])

    @patch("asyncio.create_subprocess_exec")
    async def test_schema_instruction_appended(self, mock_exec):
        mock_exec.return_value = _proc(stdout=_result_json('[{"id": 1}]'))
        result = await self.client.generate_text_async("hi", response_schema={"type": "array"})
        self.assertEqual(result, '[{"id": 1}]')
        self.assertIn("[OUTPUT REQUIREMENT]", mock_exec.call_args[0][-1])

    @patch("asyncio.create_subprocess_exec")
    async def test_command_line_overflow_raises_before_spawn(self, mock_exec):
        with patch.object(cursor_cli_client.os, "name", "nt"):
            with self.assertRaises(BtgApiInvalidRequestException) as ctx:
                await self.client.generate_text_async("가" * 40000)
        self.assertIn("청크 크기", str(ctx.exception))
        mock_exec.assert_not_called()

    @patch("asyncio.create_subprocess_exec")
    async def test_is_error_result_raises(self, mock_exec):
        mock_exec.return_value = _proc(stdout=_result_json("Something went wrong", is_error=True))
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("응답 오류", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_is_error_content_safety(self, mock_exec):
        mock_exec.return_value = _proc(stdout=_result_json("Blocked by safety policy", is_error=True))
        with self.assertRaises(BtgApiContentSafetyException):
            await self.client.generate_text_async("hi")

    @patch("asyncio.create_subprocess_exec")
    async def test_rate_limit_exit(self, mock_exec):
        mock_exec.return_value = _proc(returncode=1, stderr=b"Error: You've hit your usage limit")
        with self.assertRaises(BtgApiRateLimitException):
            await self.client.generate_text_async("hi")

    @patch("asyncio.create_subprocess_exec")
    async def test_model_outside_plan_is_not_rate_limit(self, mock_exec):
        # 2026-10-25 실호출에서 받은 문구
        mock_exec.return_value = _proc(returncode=1, stderr=(
            b"ActionRequiredError: Gemini 3.8 Flash is not included in your weekly usage "
            b"Buy credits or switch to a Grok model to continue."))
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertNotIsInstance(ctx.exception, BtgApiRateLimitException)
        self.assertIn("다른 모델", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_auth_exit(self, mock_exec):
        mock_exec.return_value = _proc(returncode=1, stderr=b"Error: Not authenticated. Run cursor-agent login")
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("인증 필요", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_timeout_kills_process(self, mock_exec):
        proc = _proc()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_exec.return_value = proc
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("시간 초과", str(ctx.exception))
        proc.kill.assert_called_once()

    @patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("missing"))
    async def test_file_not_found(self, _mock_exec):
        with self.assertRaises(BtgApiClientException) as ctx:
            await self.client.generate_text_async("hi")
        self.assertIn("실행 파일을 찾을 수 없습니다", str(ctx.exception))

    @patch("asyncio.create_subprocess_exec")
    async def test_list_models_parsed(self, mock_exec):
        mock_exec.return_value = _proc(stdout=LIST_MODELS_SAMPLE.encode("utf-8"))
        with patch("shutil.which", return_value="C:\\test\\cursor-agent.cmd"):
            models = await self.client.list_models_async()
        self.assertEqual(models[0], "auto")
        self.assertIn("grok-4.7-low-fast", models)
        self.assertIn("--list-models", mock_exec.call_args[0])

    @patch("asyncio.create_subprocess_exec")
    async def test_list_models_falls_back_on_failure(self, mock_exec):
        mock_exec.return_value = _proc(returncode=1, stderr=b"boom")
        with patch("shutil.which", return_value="C:\\test\\cursor-agent.cmd"):
            models = await self.client.list_models_async()
        self.assertEqual(models, CursorCliClient.FALLBACK_MODELS)

    @patch.object(CursorCliClient, "generate_text_async", new_callable=AsyncMock)
    async def test_check_health_success(self, mock_gen):
        mock_gen.return_value = "OK"
        with patch("shutil.which", return_value="C:\\test\\cursor-agent.cmd"):
            ok, msg = await self.client.check_health_async()
        self.assertTrue(ok)
        self.assertIn("인증 성공", msg)


class TestParseModelList(unittest.TestCase):
    def test_ids_only_without_labels_header_or_tip(self):
        self.assertEqual(
            parse_model_list(LIST_MODELS_SAMPLE),
            ["auto", "gpt-5.3-codex-low", "claude-fable-5-thinking-high", "grok-4.7-low-fast", "claude-opus-5-5-max"],
        )

    def test_empty_output(self):
        self.assertEqual(parse_model_list("Available models\n\n"), [])


class TestResolveCursorCommand(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.launcher = os.path.join(self.root, "cursor-agent.cmd")
        open(self.launcher, "w").close()

    def _make_version(self, name, complete=True):
        d = os.path.join(self.root, "versions", name)
        os.makedirs(d)
        for f in (("node.exe", "index.js") if complete else ("node.exe",)):
            open(os.path.join(d, f), "w").close()
        return d

    def _resolve(self):
        with patch.object(cursor_cli_client.os, "name", "nt"), \
             patch.object(cursor_cli_client.shutil, "which", return_value=self.launcher):
            return resolve_cursor_command("cursor-agent")

    def test_picks_latest_complete_version(self):
        self._make_version("2026.9.30-aaaaaaa")
        latest = self._make_version("2026.10.22-27dcf5f")
        self._make_version("2026.10.25-bbbbbbb", complete=False)  # 내려받는 중
        os.makedirs(os.path.join(self.root, "versions", ".extract-11360"))
        self.assertEqual(self._resolve(), [os.path.join(latest, "node.exe"), os.path.join(latest, "index.js")])

    def test_timestamped_version_format_sorts_by_time(self):
        self._make_version("2026.10.22-01-00-00-aaaaaaa")
        later = self._make_version("2026.10.22-13-00-00-bbbbbbb")
        self.assertEqual(self._resolve()[0], os.path.join(later, "node.exe"))

    def test_missing_versions_raises_instead_of_using_launcher(self):
        with self.assertRaises(BtgApiClientException):
            self._resolve()

    def test_direct_executable_used_as_is(self):
        with patch.object(cursor_cli_client.shutil, "which", return_value="/usr/local/bin/cursor-agent"):
            self.assertEqual(resolve_cursor_command("cursor-agent"), ["/usr/local/bin/cursor-agent"])


if __name__ == "__main__":
    unittest.main()
