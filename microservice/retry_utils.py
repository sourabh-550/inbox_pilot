"""
Retry policies for outside calls, and the checks that decide whether an
error is safe to retry. Imports no client that calls an API at import time,
so tests can import it directly.

Reads can be retried on any temporary failure. Writes are retried only when
the request definitely didn't happen (a rate limit, or a connection that
failed before anything was sent). After a timeout or a 5xx the first write
may have succeeded, and a retry could create a duplicate.
"""
import json
import logging

import httplib2
import httpx
import requests
from googleapiclient.errors import HttpError
from notion_client.errors import HTTPResponseError, RequestTimeoutError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger("inboxpilot.retry")

ATTEMPTS = 3
# Waits about 1s, then about 2s (never more than 4s), with up to 0.5s jitter.
WAIT = wait_exponential_jitter(initial=1, max=4, jitter=0.5)

# Google sometimes reports rate limits as 403 with one of these reasons.
GOOGLE_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


def _status(exc: BaseException):
    """HTTP status of an error response from Notion, Google or requests, else None."""
    if isinstance(exc, HTTPResponseError):
        return exc.status
    if isinstance(exc, HttpError):
        return exc.resp.status
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def _google_reasons(exc: HttpError) -> set:
    try:
        return {e.get("reason") for e in json.loads(exc.content)["error"]["errors"]}
    except Exception:
        return set()


def is_rate_limited(exc: BaseException) -> bool:
    status = _status(exc)
    if status == 429:
        return True
    return status == 403 and isinstance(exc, HttpError) and bool(_google_reasons(exc) & GOOGLE_RATE_LIMIT_REASONS)


def is_server_error(exc: BaseException) -> bool:
    status = _status(exc)
    return status is not None and status >= 500


def is_transient(exc: BaseException) -> bool:
    """Timeouts and failed or dropped connections. The request may or may not have been sent."""
    return isinstance(exc, (
        httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError,
        RequestTimeoutError,
        requests.Timeout, requests.ConnectionError,
        TimeoutError, ConnectionError, httplib2.ServerNotFoundError,
    ))


def never_sent(exc: BaseException) -> bool:
    """True only when the request definitely never reached the server."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return True
    # notion-client turns every httpx timeout into RequestTimeoutError; the
    # original httpx error is kept as its __context__.
    if isinstance(exc, RequestTimeoutError):
        return isinstance(exc.__context__, (httpx.ConnectTimeout, httpx.PoolTimeout))
    # httplib2 (Google) raises the same TimeoutError for connect and read
    # timeouts, so only a refused connection or a failed DNS lookup counts.
    if isinstance(exc, (ConnectionRefusedError, httplib2.ServerNotFoundError)):
        return True
    return isinstance(exc, requests.ConnectTimeout)


def safe_to_retry_read(exc: BaseException) -> bool:
    return is_rate_limited(exc) or is_server_error(exc) or is_transient(exc)


def safe_to_retry_write(exc: BaseException) -> bool:
    return is_rate_limited(exc) or never_sent(exc)


def _log_retry(retry_state):
    exc = retry_state.outcome.exception()
    name = getattr(retry_state.fn, "__qualname__", repr(retry_state.fn))
    logger.warning(
        f"Retrying {name} after {type(exc).__name__}: {exc} "
        f"(attempt {retry_state.attempt_number} failed, waiting {retry_state.next_action.sleep:.1f}s)"
    )


def _policy(should_retry, attempts: int):
    # reraise=True: after the last attempt the original exception comes out,
    # not tenacity's RetryError, so existing except blocks keep working.
    return retry(
        retry=retry_if_exception(should_retry),
        stop=stop_after_attempt(attempts),
        wait=WAIT,
        before_sleep=_log_retry,
        reraise=True,
    )


def read_retry(attempts: int = ATTEMPTS):
    """Decorator: retry on rate limits, 5xx, timeouts and connection errors."""
    return _policy(safe_to_retry_read, attempts)


def write_retry(attempts: int = ATTEMPTS):
    """Decorator: retry only on rate limits or a request that was never sent."""
    return _policy(safe_to_retry_write, attempts)
