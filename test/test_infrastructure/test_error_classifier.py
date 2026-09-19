"""error_classifier 단위 테스트. 429 본문은 실제 실행 로그에 남은 응답 형태를 본떴다."""
import os
import sys

import pytest
from google.api_core import exceptions as core_exc
from google.genai import errors as genai_errors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from infrastructure.error_classifier import ErrorKind, classify


def api_error(code, status, message, details=None):
    body = {"error": {"code": code, "status": status, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, body)


def quota_failure(*quota_ids, model="gemini-3.7-flash"):
    return {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
            {
                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                "quotaId": qid,
                "quotaDimensions": {"location": "global", "model": model},
                "quotaValue": "20",
            }
            for qid in quota_ids
        ],
    }


def retry_info(delay):
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


QUOTA_MSG = "You exceeded your current quota, please check your plan and billing details."


def test_daily_request_quota():
    err = api_error(429, "RESOURCE_EXHAUSTED", QUOTA_MSG,
                    [quota_failure("GenerateRequestsPerDayPerProjectPerModel-FreeTier"), retry_info("26s")])
    c = classify(err)
    assert c.kind is ErrorKind.QUOTA_DAILY
    assert c.model == "gemini-3.7-flash"
    assert c.retry_delay == 26.0  # 하루 한도인데도 몇 초짜리 retryDelay가 온다(실측). 기간 판정에 쓰지 않는다


def test_daily_token_quota_is_daily():
    err = api_error(429, "RESOURCE_EXHAUSTED", QUOTA_MSG,
                    [quota_failure("GenerateContentInputTokensPerModelPerDay-FreeTier")])
    assert classify(err).kind is ErrorKind.QUOTA_DAILY


def test_minute_request_quota_with_retry_delay():
    err = api_error(429, "RESOURCE_EXHAUSTED", QUOTA_MSG,
                    [quota_failure("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), retry_info("7s")])
    c = classify(err)
    assert c.kind is ErrorKind.QUOTA_MINUTE
    assert c.retry_delay == 7.0


def test_mixed_daily_and_minute_is_daily():
    err = api_error(429, "RESOURCE_EXHAUSTED", QUOTA_MSG, [quota_failure(
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    )])
    assert classify(err).kind is ErrorKind.QUOTA_DAILY


def test_quota_without_details_is_unknown():
    err = api_error(429, "RESOURCE_EXHAUSTED", QUOTA_MSG)
    assert classify(err).kind is ErrorKind.QUOTA_UNKNOWN


def test_quota_period_from_text_when_details_missing():
    err = Exception("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert classify(err).kind is ErrorKind.QUOTA_DAILY


def test_api_core_resource_exhausted_is_quota():
    assert classify(core_exc.ResourceExhausted("Resource has been exhausted")).kind is ErrorKind.QUOTA_UNKNOWN


def test_rate_limit_without_quota_is_transient():
    assert classify(api_error(429, "TOO_MANY_REQUESTS", "Too Many Requests")).kind is ErrorKind.TRANSIENT


def test_503_is_overloaded():
    err = api_error(503, "UNAVAILABLE", "This model is currently experiencing high demand.")
    assert classify(err).kind is ErrorKind.OVERLOADED


def test_500_is_server_500():
    assert classify(api_error(500, "INTERNAL", "An internal error has occurred.")).kind is ErrorKind.SERVER_500


@pytest.mark.parametrize("err", [
    api_error(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete."),
    core_exc.DeadlineExceeded("Deadline Exceeded"),
    Exception("The read operation timed out"),
])
def test_timeouts_are_transient_not_server_500(err):
    """504·DEADLINE_EXCEEDED는 시간 초과라 검열 후보(500)로 보지 않는다. 느린 응답에 청크가 쪼개지지 않게 한다."""
    assert classify(err).kind is ErrorKind.TRANSIENT


@pytest.mark.parametrize("err", [
    api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
    api_error(403, "PERMISSION_DENIED", "Permission denied on resource project."),
    api_error(404, "NOT_FOUND", "models/gemini-x is not found for API version v1beta"),
    core_exc.InvalidArgument("Invalid JSON payload received."),
])
def test_invalid_requests(err):
    assert classify(err).kind is ErrorKind.INVALID_REQUEST


def test_unknown_error_is_transient():
    assert classify(RuntimeError("connection reset by peer")).kind is ErrorKind.TRANSIENT
