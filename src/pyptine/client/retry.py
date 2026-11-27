"""Retry and timeout policy shared by the sync and async INE clients.

INE's behaviour under load (verified against the live API, 2026-11-26/27; see CLAUDE.md):

- It answers **404 for valid codes when throttling** a burst of requests. An unknown code is
  HTTP 200 with ``Sucesso.Falso``, never 404, so a 404 from INE's endpoints is retried.
- It resets connections and answers 429 and 5xx under load.
- Its gateway answers **504 after 120 s**. The read timeout is longer than that, so a slow
  request ends in a 504 (retried) rather than a local timeout.
- A refusal (HTTP 200 + ``Sucesso.Falso``) is final and never retried.

Retried: HTTP 404, 429, 500, 502, 503, 504; a failed or reset connection (including a connect
timeout); a malformed body (invalid JSON or XML, e.g. truncated). Not retried: a read timeout
(INE was reached but sent nothing for ``timeout`` seconds; asking again rarely helps), other
HTTP errors, and refusals.
"""

from collections.abc import Mapping, Sequence

#: HTTP statuses retried.
RETRY_STATUS = frozenset({404, 429, 500, 502, 503, 504})

#: Waits before the 1st, 2nd and 3rd retry, in seconds (as PortugalINE.jl's ``RETRY_DELAYS``).
RETRY_DELAYS: tuple[float, ...] = (5.0, 20.0, 60.0)

#: Retries after the first attempt.
MAX_RETRIES = 3

#: Longest wait honoured from a 429's Retry-After header, in seconds.
MAX_RETRY_AFTER = 120.0

#: Default read timeout, in seconds: longer than INE's 120 s gateway limit.
DEFAULT_TIMEOUT = 130

#: Connect timeout, in seconds (at most the read timeout).
CONNECT_TIMEOUT = 10.0


def waits(retry_delays: Sequence[float] | None, max_retries: int) -> list[float]:
    """The wait before each retry: ``retry_delays``, its last value repeated if needed."""
    delays = tuple(RETRY_DELAYS if retry_delays is None else retry_delays)
    if max_retries <= 0:
        return []
    if not delays:
        return [0.0] * max_retries
    return [float(delays[min(i, len(delays) - 1)]) for i in range(max_retries)]


def wait_for(status: int | None, headers: Mapping[str, str], delay: float) -> float:
    """Wait before a retry: ``delay``, or longer if a 429 asks for it with Retry-After."""
    if status == 429:
        value = headers.get("Retry-After", "")
        try:
            return min(max(delay, float(value)), MAX_RETRY_AFTER)
        except ValueError:
            pass
    return delay


def connect_timeout(timeout: float) -> float:
    return min(CONNECT_TIMEOUT, float(timeout))


def status_message(status: int, attempts: int) -> str:
    """Error text for a retried status that did not clear."""
    text = f"HTTP {status} after {attempts} attempt(s)"
    if status == 404:
        text += (
            ". INE answers 404 when it throttles requests (an unknown indicator code is HTTP 200 "
            "with a refusal, never 404); wait and try again, or space out requests"
        )
    elif status == 504:
        text += ". INE's gateway gives up after 120 s; filter more dimensions or fewer periods"
    return text


def read_timeout_message(timeout: float) -> str:
    return (
        f"No response from INE within {timeout:g} s (read timeout; not retried). INE's gateway "
        "gives up after 120 s: filter more dimensions or fewer periods, or raise `timeout`"
    )


class RetryableError(Exception):
    """An attempt that may succeed if repeated; ``error`` is raised when retries run out."""

    def __init__(
        self,
        error: Exception,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        # The message is the short reason logged before a retry; ``error`` is the final error.
        super().__init__(f"HTTP {status}" if status is not None else str(error))
        self.error = error
        self.status = status
        self.headers: Mapping[str, str] = headers or {}
