"""Structured JSON logging for the Koncile MCP server.

One line per tool call, so we can answer "who is using this, and what are they
calling" — which, before this existed, the hosted server could not answer at all.

Deliberately stdlib `logging` with a small JSON formatter rather than structlog:
this package is published to PyPI with two runtime dependencies, and a single
event type does not justify dragging a logging framework into every user's
environment.

Two rules that are easy to break and fail silently:

* **Never log to stdout.** The stdio transport *is* stdout — a log line there
  corrupts the JSON-RPC stream and hangs the client with no error message. The
  handler is hard-wired to stderr and there is no option to change it.
* **Every line must be exactly one JSON object with no prefix**, or CloudWatch
  stops auto-discovering the fields and every dashboard query silently returns
  nothing. That is why `configure_logging` also quiets uvicorn's access log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "koncile_mcp"

_logger = logging.getLogger(LOGGER_NAME)

# Only these argument names are ever logged, and only when they are scalars.
# Everything else on a tool call is customer data: file names, file content,
# instruction bodies, folder and template names, extracted values. An allowlist
# is the only shape that stays safe when a new tool adds a new argument.
_LOGGABLE_ARGS = frozenset({
    "document_id",
    "doc_id",
    "task_id",
    "folder_id",
    "template_id",
    "field_id",
    "instruction_id",
    "duplicate_from",
    "override",
    "delete_file",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": "koncile-mcp",
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Install the JSON handler. Call once, before uvicorn starts.

    uvicorn installs its own formatters when it boots, so calling this
    afterwards (or without passing ``log_config=None`` to ``uvicorn.run``) means
    plain-text access lines interleave with the JSON and break field discovery.
    """
    handler = logging.StreamHandler(sys.stderr)  # never stdout — see module docstring
    handler.setFormatter(_JsonFormatter())

    _logger.handlers[:] = [handler]
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

    # The ALB health-checks every few seconds. Left on, those lines are ~99% of
    # the log group and both drown the tool calls and cost money to scan.
    logging.getLogger("uvicorn.access").disabled = True


def caller_fingerprint(api_key: str | None) -> str | None:
    """A salted, truncated, one-way id for an API key.

    Same algorithm and same salt as the backend's `caller_key_fp`, so a given
    account has the same fingerprint in `/ecs/mcp-prod` and in the API's logs and
    the two can be joined — without either side ever logging the key.

    An unset salt still yields a one-way hash, but one that does *not* match the
    backend's. That produces an island of unjoinable data with no error to warn
    you, so `KONCILE_API_KEY_LOG_HASH_SALT` belongs on the deploy checklist.
    """
    if not api_key:
        return None
    # API_KEY_LOG_HASH_SALT is the name in the Api deploy doc (mcp-server-deploy.md).
    salt = os.environ.get("KONCILE_API_KEY_LOG_HASH_SALT") or os.environ.get(
        "API_KEY_LOG_HASH_SALT", ""
    )
    return hashlib.sha256(f"{salt}{api_key}".encode()).hexdigest()[:16]


def summarise_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce tool arguments to the scalar ids that are safe to log."""
    if not args:
        return {}
    summary: dict[str, Any] = {}
    for key, value in args.items():
        if key in _LOGGABLE_ARGS and isinstance(value, (str, int, float, bool)):
            summary[key] = value
    # Shape, not content: enough to spot a runaway batch without logging names.
    if isinstance(args.get("filenames"), list):
        summary["file_count"] = len(args["filenames"])
    if isinstance(args.get("upload_ids"), list):
        summary["upload_count"] = len(args["upload_ids"])
    if args.get("file_path"):
        summary["has_file_path"] = True
    if args.get("file_content"):
        summary["has_file_content"] = True
    return summary


def log_event(event: str, **fields: Any) -> None:
    """Emit one structured line."""
    _logger.info(event, extra={"fields": {"event": event, **fields}})
