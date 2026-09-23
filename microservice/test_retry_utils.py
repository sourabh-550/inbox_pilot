"""
Tests for retry_utils. Run from inside microservice/ with either:
    python test_retry_utils.py
    pytest test_retry_utils.py
No API calls: the errors are built by hand.
"""
import json

import httplib2
import httpx
import requests
from googleapiclient.errors import HttpError
from notion_client.errors import RequestTimeoutError, UnknownHTTPResponseError
from tenacity import wait_none

from retry_utils import read_retry, write_retry, safe_to_retry_read, safe_to_retry_write


def _notion(status):
    return UnknownHTTPResponseError(status=status)


def _notion_timeout(cause=None):
    # notion-client raises RequestTimeoutError inside `except httpx.TimeoutException`,
    # so the httpx error ends up as __context__.
    exc = RequestTimeoutError()
    exc.__context__ = cause
    return exc


def _google(status, reason=None):
    content = b""
    if reason:
        content = json.dumps({"error": {"errors": [{"reason": reason}], "message": reason}}).encode()
    return HttpError(httplib2.Response({"status": status}), content)


def _requests_http(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


RATE_LIMITS = [
    _notion(429),
    _google(429),
    _google(403, "rateLimitExceeded"),
    _google(403, "userRateLimitExceeded"),
    _requests_http(429),
]

SERVER_ERRORS = [_notion(500), _notion(503), _google(500), _google(503), _requests_http(502)]

NEVER_SENT = [
    httpx.ConnectError("refused"),
    httpx.ConnectTimeout("connect timeout"),
    httpx.PoolTimeout("pool timeout"),
    _notion_timeout(httpx.ConnectTimeout("connect timeout")),
    ConnectionRefusedError(),
    httplib2.ServerNotFoundError("dns"),
    requests.ConnectTimeout(),
]

# Timeouts or dropped connections where the request may already have arrived.
MAYBE_SENT = [
    _notion_timeout(httpx.ReadTimeout("read timeout")),
    _notion_timeout(),
    httpx.ReadTimeout("read timeout"),
    httpx.RemoteProtocolError("disconnected"),
    TimeoutError(),
    ConnectionResetError(),
    requests.ReadTimeout(),
    requests.ConnectionError(),
]

NOT_RETRYABLE = [
    _notion(400),
    _notion(404),
    _google(400),
    _google(403, "forbidden"),
    _google(404),
    _requests_http(400),
    ValueError("bad value"),
    KeyError("missing"),
]


def test_read_retries_rate_limits():
    for exc in RATE_LIMITS:
        assert safe_to_retry_read(exc), exc


def test_read_retries_server_errors():
    for exc in SERVER_ERRORS:
        assert safe_to_retry_read(exc), exc


def test_read_retries_timeouts_and_connection_errors():
    for exc in NEVER_SENT + MAYBE_SENT:
        assert safe_to_retry_read(exc), exc


def test_read_does_not_retry_client_errors():
    for exc in NOT_RETRYABLE:
        assert not safe_to_retry_read(exc), exc


def test_write_retries_rate_limits():
    for exc in RATE_LIMITS:
        assert safe_to_retry_write(exc), exc


def test_write_retries_requests_that_were_never_sent():
    for exc in NEVER_SENT:
        assert safe_to_retry_write(exc), exc


def test_write_does_not_retry_server_errors():
    for exc in SERVER_ERRORS:
        assert not safe_to_retry_write(exc), exc


def test_write_does_not_retry_when_request_may_have_been_sent():
    for exc in MAYBE_SENT:
        assert not safe_to_retry_write(exc), exc


def test_write_does_not_retry_client_errors():
    for exc in NOT_RETRYABLE:
        assert not safe_to_retry_write(exc), exc


def _failing(errors):
    """A function that raises each error in turn, then returns "ok"; counts calls."""
    calls = []

    def fn():
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "ok"

    return fn, calls


def test_read_policy_retries_then_succeeds():
    fn, calls = _failing([_notion(503)])
    assert read_retry()(fn).retry_with(wait=wait_none())() == "ok"
    assert len(calls) == 2


def test_policy_gives_up_after_three_attempts_with_original_error():
    error = _notion(503)
    fn, calls = _failing([error] * 5)
    try:
        read_retry()(fn).retry_with(wait=wait_none())()
    except UnknownHTTPResponseError as e:
        assert e is error
        assert len(calls) == 3
        return
    raise AssertionError("expected the original error after 3 attempts")


def test_write_policy_does_not_retry_a_read_timeout():
    error = httpx.ReadTimeout("read timeout")
    fn, calls = _failing([error])
    try:
        write_retry()(fn).retry_with(wait=wait_none())()
    except httpx.ReadTimeout as e:
        assert e is error
        assert len(calls) == 1
        return
    raise AssertionError("expected ReadTimeout without a retry")


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"all {len(tests)} tests passed")
