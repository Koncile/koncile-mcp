"""Translate HTTP errors into human-readable MCP error text."""

from __future__ import annotations

import httpx


class KoncileAPIError(RuntimeError):
    """An error response from the Koncile API, with the status kept.

    Subclasses ``RuntimeError`` on purpose: everything previously raised a bare
    ``RuntimeError`` with the status buried inside a formatted string, so callers
    (and tests) that catch ``RuntimeError`` keep working. The difference is that
    the status is now readable, which is what lets the request logger classify an
    outcome as `rate_limited` or `upstream_error` instead of a flat `error`.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        value = resp.json().get("retry_after_seconds")
        return float(value) if value is not None else None
    except Exception:
        return None


def format_http_error(resp: httpx.Response) -> str:
    """Return a user-facing error string for a non-2xx response."""
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text

    status = resp.status_code
    if status in (401, 403):
        return f"Authentication/permission error: {detail}"
    if status == 404:
        return f"Not found: {detail}"
    if status == 422:
        return f"Validation error: {detail}"
    if status == 429:
        # The model reads this string and decides what to do next, so it has to
        # say "wait" explicitly. Without that, the usual reaction to an error is
        # to try again immediately, which is exactly what a rate limit is for.
        retry_after = _retry_after_seconds(resp)
        wait = (
            f"Retry after {retry_after:g} seconds."
            if retry_after is not None
            else "Retry after a short pause."
        )
        return (
            f"Rate limited by the Koncile API (429). {wait} "
            "Wait the full interval before calling again — do not retry in a loop."
        )
    if status >= 500:
        return f"Server error: {detail}"
    return f"HTTP {status}: {detail}"


def http_error(resp: httpx.Response) -> KoncileAPIError:
    """Build the exception to raise for a non-2xx response."""
    return KoncileAPIError(
        format_http_error(resp),
        status=resp.status_code,
        retry_after=_retry_after_seconds(resp),
    )


def format_connection_error(url: str, exc: Exception) -> str:
    return f"Cannot reach Koncile API at {url}: {exc}"
