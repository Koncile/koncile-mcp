"""Tests for errors.py — HTTP error formatting."""

import httpx

from koncile_mcp.errors import (
    KoncileAPIError,
    format_connection_error,
    format_http_error,
    http_error,
)


def _make_response(
    status: int,
    json_body: dict | None = None,
    text: str = "",
    headers: dict | None = None,
) -> httpx.Response:
    all_headers = dict(headers or {})
    if json_body is not None:
        import json
        all_headers["content-type"] = "application/json"
        return httpx.Response(status, text=json.dumps(json_body), headers=all_headers)
    return httpx.Response(status, text=text, headers=all_headers)


def test_401_error():
    resp = _make_response(401, {"detail": "Invalid API key"})
    assert format_http_error(resp) == "Authentication/permission error: Invalid API key"


def test_403_error():
    resp = _make_response(403, {"detail": "Insufficient permissions"})
    assert format_http_error(resp) == "Authentication/permission error: Insufficient permissions"


def test_404_error():
    resp = _make_response(404, {"detail": "Folder not found"})
    assert format_http_error(resp) == "Not found: Folder not found"


def test_422_error():
    resp = _make_response(422, {"detail": "name is required"})
    assert format_http_error(resp) == "Validation error: name is required"


def test_500_error():
    resp = _make_response(500, {"detail": "Internal server error"})
    assert format_http_error(resp) == "Server error: Internal server error"


def test_502_error():
    resp = _make_response(502, {"detail": "Bad gateway"})
    assert format_http_error(resp) == "Server error: Bad gateway"


def test_generic_4xx_error():
    resp = _make_response(418, {"detail": "Teapot"})
    assert format_http_error(resp) == "HTTP 418: Teapot"


def test_429_tells_the_model_to_wait():
    # The model reads this string and decides what to do next. Without an
    # explicit instruction to wait, the natural reaction to an error is to try
    # again immediately — which is what the rate limit exists to stop.
    resp = _make_response(429, {"detail": "Rate limited"})
    msg = format_http_error(resp)
    assert "429" in msg
    assert "do not retry in a loop" in msg


def test_429_reports_retry_after_from_the_header():
    resp = _make_response(429, {"detail": "Rate limited"}, headers={"retry-after": "12"})
    assert "Retry after 12 seconds" in format_http_error(resp)


def test_429_falls_back_to_the_body_field():
    # The API sends both; the header is authoritative, the body is the fallback.
    resp = _make_response(429, {"detail": "Rate limited", "retry_after_seconds": 7})
    assert "Retry after 7 seconds" in format_http_error(resp)


def test_http_error_keeps_the_status_readable():
    # The status used to be buried in a formatted string, so nothing downstream
    # could tell a rate limit from a validation error.
    exc = http_error(_make_response(429, {"detail": "Rate limited"},
                                    headers={"retry-after": "5"}))
    assert isinstance(exc, KoncileAPIError)
    assert isinstance(exc, RuntimeError)  # existing callers still catch it
    assert exc.status == 429
    assert exc.retry_after == 5.0


def test_non_json_response():
    resp = _make_response(500, text="Gateway Timeout")
    assert "Gateway Timeout" in format_http_error(resp)


def test_json_without_detail_key():
    resp = _make_response(400, {"error": "bad request"})
    # Falls back to resp.text since "detail" key is missing
    msg = format_http_error(resp)
    assert "400" in msg or "bad request" in msg


def test_format_connection_error():
    exc = ConnectionRefusedError("Connection refused")
    msg = format_connection_error("http://localhost:8000", exc)
    assert "Cannot reach Koncile API at http://localhost:8000" in msg
    assert "Connection refused" in msg
