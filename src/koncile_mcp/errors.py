"""Translate HTTP errors into human-readable MCP error text."""

from __future__ import annotations

import httpx


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
    if status >= 500:
        return f"Server error: {detail}"
    return f"HTTP {status}: {detail}"


def format_connection_error(url: str, exc: Exception) -> str:
    return f"Cannot reach Koncile API at {url}: {exc}"
