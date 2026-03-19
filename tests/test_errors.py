"""Tests for errors.py — HTTP error formatting."""

import httpx

from koncile_mcp.errors import format_http_error, format_connection_error


def _make_response(status: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    if json_body is not None:
        import json
        return httpx.Response(status, text=json.dumps(json_body), headers={"content-type": "application/json"})
    return httpx.Response(status, text=text)


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
    resp = _make_response(429, {"detail": "Rate limited"})
    assert format_http_error(resp) == "HTTP 429: Rate limited"


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
