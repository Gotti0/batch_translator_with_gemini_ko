"""retry_policy 단위 테스트: 일시 오류 재시도, 500 연속 판정, 할당량 쿨다운, 하루 한도 리셋 시각."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import infrastructure.retry_policy as rp
from infrastructure.error_classifier import ClassifiedError, ErrorKind
from infrastructure.retry_policy import Action, RetryPolicy, seconds_until_daily_reset

MARGIN = 60.0


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def policy(max_retries=3, wall=0.0):
    return RetryPolicy(max_retries, initial_backoff=2.0, max_backoff=5.0, jitter=lambda: 0.0, wall_clock=lambda: wall)


E = ClassifiedError


def test_transient_retries_with_capped_backoff_then_gives_up():
    p = policy(max_retries=3)
    decisions = [p.on_error(E(ErrorKind.TRANSIENT)) for _ in range(4)]
    assert [d.action for d in decisions] == [Action.RETRY_SAME_KEY] * 3 + [Action.FAIL_RETRIES_EXHAUSTED]
    assert [d.delay for d in decisions[:3]] == [2.0, 4.0, 5.0]
    assert p.attempt == 3


def test_jitter_is_added_to_backoff():
    p = RetryPolicy(2, 2.0, 60.0, jitter=lambda: 0.5, wall_clock=lambda: 0.0)
    assert p.on_error(E(ErrorKind.TRANSIENT)).delay == 2.5


def test_consecutive_500_is_content_safety():
    p = policy()
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.RETRY_SAME_KEY
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.FAIL_SAFETY


def test_500_streak_is_broken_by_other_errors():
    p = policy(max_retries=5)
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.RETRY_SAME_KEY
    assert p.on_error(E(ErrorKind.OVERLOADED)).action is Action.RETRY_SAME_KEY
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.RETRY_SAME_KEY


def test_500_streak_is_broken_by_key_switch():
    p = policy()
    p.on_error(E(ErrorKind.SERVER_500))
    p.on_error(E(ErrorKind.QUOTA_UNKNOWN))
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.RETRY_SAME_KEY


def test_first_500_without_retry_budget_is_not_safety():
    p = policy(max_retries=0)
    assert p.on_error(E(ErrorKind.SERVER_500)).action is Action.FAIL_RETRIES_EXHAUSTED


def test_invalid_request_switches_key_without_cooldown():
    d = policy().on_error(E(ErrorKind.INVALID_REQUEST))
    assert d.action is Action.SWITCH_KEY
    assert d.cooldown_seconds is None


def test_minute_quota_uses_server_retry_delay():
    d = policy().on_error(E(ErrorKind.QUOTA_MINUTE, retry_delay=7.0, model="m"))
    assert (d.action, d.cooldown_seconds, d.cooldown_model) == (Action.SWITCH_KEY, 7.0, "m")


def test_minute_quota_defaults_to_60_seconds():
    assert policy().on_error(E(ErrorKind.QUOTA_MINUTE)).cooldown_seconds == 60.0


def test_unknown_quota_cools_down_100_seconds_for_all_models():
    d = policy().on_error(E(ErrorKind.QUOTA_UNKNOWN))
    assert (d.cooldown_seconds, d.cooldown_model) == (100.0, None)


def test_daily_quota_cools_down_until_pacific_midnight():
    now = utc(2026, 9, 19, 19, 0)  # 12:00 PDT
    d = policy(wall=now).on_error(E(ErrorKind.QUOTA_DAILY, retry_delay=26.0, model="m"))
    assert d.action is Action.SWITCH_KEY
    assert d.cooldown_model == "m"
    assert d.cooldown_seconds == 12 * 3600 + MARGIN  # retryDelay(26초)는 무시


def test_quota_switch_does_not_use_retry_budget():
    p = policy(max_retries=1)
    p.on_error(E(ErrorKind.QUOTA_DAILY))
    p.on_error(E(ErrorKind.INVALID_REQUEST))
    assert p.attempt == 0
    assert p.on_error(E(ErrorKind.OVERLOADED)).action is Action.RETRY_SAME_KEY


@pytest.mark.parametrize("now, expected_hours", [
    (utc(2026, 9, 19, 19, 0), 12.0),     # PDT 정오 → 다음 날 07:00 UTC
    (utc(2026, 1, 15, 20, 0), 12.0),     # PST 정오 → 다음 날 08:00 UTC
    (utc(2026, 11, 1, 7, 30), 24.5),     # 11/1 00:30 PDT, 이날 02:00에 PST로 바뀜 → 11/2 08:00 UTC
    (utc(2026, 3, 8, 8, 30), 22.5),      # 3/8 00:30 PST, 이날 02:00에 PDT로 바뀜 → 3/9 07:00 UTC
])
def test_daily_reset_handles_daylight_saving(now, expected_hours):
    assert seconds_until_daily_reset(now) == expected_hours * 3600 + MARGIN


def test_daily_reset_falls_back_to_pst_without_tzdata(monkeypatch):
    monkeypatch.setattr(rp, "_PACIFIC", None)
    # PDT 기간 정오(19:00 UTC). PST 고정이면 자정은 08:00 UTC → 13시간 (실제보다 1시간 늦음, 일찍 풀리지는 않음)
    assert seconds_until_daily_reset(utc(2026, 9, 19, 19, 0)) == 13 * 3600 + MARGIN
