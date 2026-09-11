"""Tests for the structured logging and the instrumentation seam."""

import json
import logging
import sys

import httpx
import pytest
from unittest.mock import AsyncMock

from koncile_mcp import logging as klog
from koncile_mcp.client import KoncileClient
from koncile_mcp.errors import KoncileAPIError
from koncile_mcp.instrumentation import _classify, dispatch


@pytest.fixture
def mock_client():
    return AsyncMock(spec=KoncileClient)


@pytest.fixture
def captured(monkeypatch):
    """Capture emitted JSON lines."""
    lines = []

    class _Sink(logging.Handler):
        def emit(self, record):
            lines.append(json.loads(klog._JsonFormatter().format(record)))

    logger = logging.getLogger(klog.LOGGER_NAME)
    monkeypatch.setattr(logger, "handlers", [_Sink()])
    logger.setLevel(logging.INFO)
    return lines


class TestHandlerDestination:
    def test_never_writes_to_stdout(self):
        # stdout IS the stdio transport. A log line there corrupts the JSON-RPC
        # stream and hangs the client with no error to explain it.
        klog.configure_logging()
        handlers = logging.getLogger(klog.LOGGER_NAME).handlers
        assert handlers
        for h in handlers:
            assert h.stream is sys.stderr

    def test_health_check_noise_is_silenced(self):
        # The ALB health-checks every few seconds; left on, those lines are the
        # overwhelming majority of the log group.
        klog.configure_logging()
        assert logging.getLogger("uvicorn.access").disabled


class TestFingerprint:
    def test_is_stable_and_does_not_contain_the_key(self, monkeypatch):
        monkeypatch.setenv("KONCILE_API_KEY_LOG_HASH_SALT", "s")
        fp = klog.caller_fingerprint("sk-secret-value")
        assert fp == klog.caller_fingerprint("sk-secret-value")
        assert len(fp) == 16
        assert "secret" not in fp

    def test_matches_the_backend_algorithm(self, monkeypatch):
        # The whole point: the same account must have the same id in the MCP
        # logs and the API's, so the two can be joined. A mismatch produces an
        # island of unjoinable data with no error to warn you.
        import hashlib
        monkeypatch.setenv("KONCILE_API_KEY_LOG_HASH_SALT", "shared")
        expected = hashlib.sha256(b"sharedsk-abc").hexdigest()[:16]
        assert klog.caller_fingerprint("sk-abc") == expected

    def test_no_key_no_fingerprint(self):
        assert klog.caller_fingerprint(None) is None


class TestArgSummary:
    def test_keeps_scalar_ids(self):
        out = klog.summarise_args({"document_id": 7, "task_id": "t-1"})
        assert out == {"document_id": 7, "task_id": "t-1"}

    def test_drops_customer_data(self):
        # Filenames, file contents and instruction bodies are the customer's
        # documents. An allowlist is the only shape that stays safe when a new
        # tool adds a new argument.
        out = klog.summarise_args({
            "file_content": "JVBERi0x...",
            "file_name": "Acme Ltd - invoice 2026-03.pdf",
            "content": "the supplier is always ACME",
            "name": "Acme supplier folder",
            "metadata": '{"client":"Acme"}',
            "document_id": 7,
        })
        assert out == {"document_id": 7, "has_file_content": True}

    def test_records_shape_not_content(self):
        out = klog.summarise_args({"filenames": ["a.pdf", "b.pdf"], "upload_ids": [1]})
        assert out == {"file_count": 2, "upload_count": 1}
        assert "a.pdf" not in json.dumps(out)


class TestOutcomeClassification:
    @pytest.mark.parametrize("exc,expected", [
        (KoncileAPIError("x", status=429), "rate_limited"),
        (KoncileAPIError("x", status=503), "upstream_error"),
        (KoncileAPIError("x", status=404), "error"),
        (httpx.ConnectTimeout("x"), "upstream_error"),
        (ValueError("missing param"), "invalid_input"),
        (RuntimeError("?"), "error"),
    ])
    def test_classification(self, exc, expected):
        assert _classify(exc)[0] == expected


class TestDispatchEmitsOneEvent:
    @pytest.mark.asyncio
    async def test_success(self, captured, mock_client):
        mock_client.get.return_value = {"folders": []}
        await dispatch(mock_client, "list_folders", {}, transport="http",
                       caller_key_fp="abc123")
        assert len(captured) == 1
        event = captured[0]
        assert event["event"] == "tool_call"
        assert event["tool"] == "list_folders"
        assert event["outcome"] == "ok"
        assert event["caller_key_fp"] == "abc123"
        assert event["transport"] == "http"
        assert isinstance(event["duration_ms"], (int, float))

    @pytest.mark.asyncio
    async def test_failure_is_logged_and_still_raised(self, captured, mock_client):
        mock_client.get.side_effect = KoncileAPIError("rate limited", status=429)
        with pytest.raises(KoncileAPIError):
            await dispatch(mock_client, "list_folders", {}, transport="http")
        assert captured[0]["outcome"] == "rate_limited"
        assert captured[0]["http_status"] == 429

    @pytest.mark.asyncio
    async def test_capability_is_threaded_through(self, captured, mock_client):
        with pytest.raises(ValueError, match="not supported on the hosted"):
            await dispatch(mock_client, "upload_file", {"file_path": "/x/y.pdf"},
                           transport="http", allow_local_files=False)
        assert captured[0]["outcome"] == "invalid_input"
