"""The one place every tool call passes through, so it can be timed and logged.

Deliberately *not* inside ``handle_tool``: that is the pure unit the test suite
calls directly with a mock client, and instrumenting it would put a log line in
every one of ~2000 lines of tests. Deliberately *not* Starlette middleware
either: one POST to /mcp carries one JSON-RPC message, and middleware sees
`initialize` and `tools/list` identically to `tools/call` — it has no tool name
to log.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from mcp.types import TextContent

from koncile_mcp.client import KoncileClient
from koncile_mcp.errors import KoncileAPIError
from koncile_mcp.logging import log_event, summarise_args
from koncile_mcp.server import handle_tool


def _classify(exc: Exception) -> tuple[str, int | None]:
    """Map an exception to (outcome, http_status) for the dashboard."""
    if isinstance(exc, KoncileAPIError):
        status = exc.status
        if status == 429:
            return "rate_limited", status
        if status is not None and status >= 500:
            return "upstream_error", status
        return "error", status
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return "upstream_error", None
    if isinstance(exc, ValueError):
        # `_require` and the unknown-tool branch both raise ValueError.
        return "invalid_input", None
    return "error", None


async def dispatch(
    client: KoncileClient,
    name: str,
    args: dict[str, Any] | None,
    *,
    transport: str,
    caller_key_fp: str | None = None,
    allow_local_files: bool = False,
) -> list[TextContent]:
    """Run a tool and emit exactly one ``tool_call`` event for it."""
    started = time.perf_counter()
    base = {
        "tool": name,
        "transport": transport,
        "caller_key_fp": caller_key_fp,
        "args": summarise_args(args),
    }
    try:
        result = await handle_tool(
            client, name, args or {}, allow_local_files=allow_local_files
        )
    except Exception as exc:
        outcome, status = _classify(exc)
        log_event(
            "tool_call",
            outcome=outcome,
            http_status=status,
            error_type=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            **base,
        )
        raise

    log_event(
        "tool_call",
        outcome="ok",
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        **base,
    )
    return result
