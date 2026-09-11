"""Koncile MCP Server — Streamable HTTP transport for hosted deployment."""

from __future__ import annotations

import contextvars
import os
from typing import Any

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from koncile_mcp.client import KoncileClient
from koncile_mcp.config import DEFAULT_API_URL, Config
from koncile_mcp.instrumentation import dispatch
from koncile_mcp.logging import caller_fingerprint, configure_logging, log_event
from koncile_mcp.server import SERVER_INSTRUCTIONS, tools_for

# Per-request API key, set by auth middleware before tool handlers run.
_current_api_key: contextvars.ContextVar[str] = contextvars.ContextVar("_current_api_key")


def _config_from_bearer(api_key: str) -> Config:
    """Build a Config using the caller's Bearer token as the API key."""
    api_url = os.environ.get("KONCILE_API_URL", DEFAULT_API_URL).rstrip("/")
    timeout = float(os.environ.get("KONCILE_REQUEST_TIMEOUT", "120"))
    return Config(api_url=api_url, api_key=api_key, request_timeout=timeout)


class _AuthenticatedMCPEndpoint:
    """ASGI app that extracts Bearer token and delegates to the session manager."""

    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive, send)

        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            # This is the first thing a new user sees when their key is not set,
            # so it has to say what to do rather than just what is wrong.
            response = Response(
                "Missing or invalid Authorization header. Send your Koncile API "
                "key as \"Authorization: Bearer <key>\". If you are using the "
                "Claude Code plugin or a local MCP client, set KONCILE_API_KEY "
                "in your environment. You can create a key in the Koncile app "
                "under Settings > API.",
                status_code=401,
            )
            await response(scope, receive, send)
            return

        _current_api_key.set(token)
        await self._session_manager.handle_request(scope, receive, send)


def create_http_app() -> Starlette:
    """Create a Starlette ASGI app serving the MCP protocol over Streamable HTTP."""

    server = Server("koncile", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        # Hosted: no local filesystem to read from, so upload_file is advertised
        # without file_path rather than offering a parameter that always errors.
        return tools_for(allow_local_files=False)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        api_key = _current_api_key.get()
        config = _config_from_bearer(api_key)
        client = KoncileClient(config)
        try:
            return await dispatch(
                client,
                name,
                arguments or {},
                transport="http",
                caller_key_fp=caller_fingerprint(api_key),
                allow_local_files=False,
            )
        finally:
            await client.close()

    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=False,
    )

    mcp_endpoint = _AuthenticatedMCPEndpoint(session_manager)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Two explicit routes rather than Route + Mount. The Mount was not redundant
    # — it is what served "/mcp/" — but dropping to one route would leave a
    # client posting to the trailing-slash form getting a 307, and httpx does
    # not follow redirects by default.
    app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Route("/mcp", endpoint=mcp_endpoint),
            Route("/mcp/", endpoint=mcp_endpoint),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    return app


def main() -> None:
    """Entry point — run the MCP server over Streamable HTTP."""
    import uvicorn

    # Before uvicorn.run, and with log_config=None below: uvicorn installs its
    # own formatters on startup, and plain-text access lines interleaved with
    # the JSON break CloudWatch's field discovery, which every dashboard query
    # depends on.
    configure_logging()

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    log_event(
        "server_start",
        transport="http",
        api_url=os.environ.get("KONCILE_API_URL", DEFAULT_API_URL),
        salt_configured=bool(os.environ.get("KONCILE_API_KEY_LOG_HASH_SALT")),
    )

    app = create_http_app()
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
