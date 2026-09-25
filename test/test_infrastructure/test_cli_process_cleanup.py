"""
CLI 클라이언트 서브프로세스 정리와 Antigravity 할당량 소진 처리 테스트.

헬스체크는 바깥 asyncio.wait_for로 제한 시간을 거는데, 이때 안쪽 호출에는 TimeoutError가 아니라
취소가 전달된다. 예전에는 kill이 TimeoutError 경로에만 있어서 CLI 프로세스가 살아남았다.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import BtgApiClientException, BtgApiRateLimitException
from infrastructure.antigravity_cli_client import AntigravityCliClient
from infrastructure.claude_cli_client import ClaudeCliClient
from infrastructure.codex_cli_client import CodexCliClient

AGY_QUOTA_ERROR = (
    "error: RESOURCE_EXHAUSTED (code 429): Resource has been exhausted (e.g. check quota).\n"
    'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429)","status":"RESOURCE_EXHAUSTED",'
    '"error_code":429,"retryable":true,"error_id":"28899b40-9b8b-44be-838c-3d0eed877747-9-2008"}'
)


def _hanging_proc():
    """communicate()가 끝나지 않는 실행 중 프로세스."""
    proc = MagicMock()
    proc.returncode = None

    async def hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    proc.communicate = hang
    proc.kill = MagicMock()
    return proc


def _finished_proc(returncode, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.parametrize("client", [
    ClaudeCliClient(cli_path="claude"),
    CodexCliClient(cli_path="codex"),
    AntigravityCliClient(cli_path="agy"),
], ids=["claude", "codex", "antigravity"])
def test_outer_timeout_kills_cli_process(client):
    proc = _hanging_proc()

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.generate_text_async("hi"), timeout=0.05)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(run())
    proc.kill.assert_called_once()


def test_finished_process_is_not_killed():
    proc = _finished_proc(0, stdout=b'{"status": "SUCCESS", "response": "OK"}')
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        assert asyncio.run(AntigravityCliClient(cli_path="agy").generate_text_async("hi")) == "OK"
    proc.kill.assert_not_called()


def test_agy_quota_exhausted_is_rate_limit():
    proc = _finished_proc(3, stderr=AGY_QUOTA_ERROR.encode("utf-8"))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(BtgApiRateLimitException):
            asyncio.run(AntigravityCliClient(cli_path="agy").generate_text_async("hi"))


def test_agy_json_error_status_quota_is_rate_limit():
    body = b'{"status": "ERROR", "response": "RESOURCE_EXHAUSTED (code 429)"}'
    proc = _finished_proc(0, stdout=body)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(BtgApiRateLimitException):
            asyncio.run(AntigravityCliClient(cli_path="agy").generate_text_async("hi"))


def test_agy_other_failure_stays_client_error():
    proc = _finished_proc(1, stderr=b"error: something else broke (error_id 1429-abc)")
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(BtgApiClientException) as info:
            asyncio.run(AntigravityCliClient(cli_path="agy").generate_text_async("hi"))
    assert not isinstance(info.value, BtgApiRateLimitException)


def test_agy_health_reports_quota_exhaustion():
    client = AntigravityCliClient(cli_path="agy")
    with patch("infrastructure.antigravity_cli_client.shutil.which", return_value="agy"), \
         patch.object(client, "generate_text_async",
                      AsyncMock(side_effect=BtgApiRateLimitException("Antigravity CLI 사용량 제한: RESOURCE_EXHAUSTED"))):
        ok, message = asyncio.run(client.check_health_async())
    assert ok is False
    assert "할당량 소진" in message


def test_agy_health_timeout_mentions_quota():
    client = AntigravityCliClient(cli_path="agy")
    with patch("infrastructure.antigravity_cli_client.shutil.which", return_value="agy"), \
         patch.object(client, "generate_text_async", AsyncMock(side_effect=asyncio.TimeoutError)):
        ok, message = asyncio.run(client.check_health_async())
    assert ok is False
    assert "시간 초과" in message and "할당량" in message


def test_agy_list_models_timeout_kills_process():
    proc = _hanging_proc()
    client = AntigravityCliClient(cli_path="agy")
    real_wait_for = asyncio.wait_for

    async def short_wait_for(aw, timeout):
        return await real_wait_for(aw, timeout=0.05)

    with patch("infrastructure.antigravity_cli_client.shutil.which", return_value="agy"), \
         patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
         patch("infrastructure.antigravity_cli_client.asyncio.wait_for", short_wait_for):
        models = asyncio.run(client.list_models_async())
    assert models == list(AntigravityCliClient.FALLBACK_MODELS)
    proc.kill.assert_called_once()
