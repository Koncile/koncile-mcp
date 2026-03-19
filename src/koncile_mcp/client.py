"""Async HTTP client wrapping the Koncile public API."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from koncile_mcp.config import Config
from koncile_mcp.errors import format_http_error, format_connection_error


class KoncileClient:
    """Thin async wrapper around the Koncile /v1/* public API."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.request_timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    # -- low-level helpers --------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        files: Any | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        # POST/PUT/DELETE must have trailing slash (API returns 307 without it)
        if method.upper() in ("POST", "PUT", "DELETE") and not path.endswith("/"):
            path = path + "/"

        try:
            resp = await self._http.request(
                method, path, params=params, json=json, files=files, data=data,
            )
        except httpx.ConnectError as exc:
            raise RuntimeError(format_connection_error(self._config.api_url, exc)) from exc

        if resp.status_code >= 400:
            raise RuntimeError(format_http_error(resp))

        return resp

    async def get(self, path: str, **params: Any) -> Any:
        # Filter out None values from params
        clean = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("GET", path, params=clean or None)
        return resp.json()

    async def post_json(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        clean_params = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("POST", path, json=body, params=clean_params or None)
        return resp.json()

    async def post_file(
        self,
        path: str,
        file_content_b64: str,
        file_name: str,
        **params: Any,
    ) -> Any:
        """Upload a file via multipart/form-data. Content is base64-encoded."""
        raw = base64.b64decode(file_content_b64)
        files = [("files", (file_name, raw))]
        clean_params = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("POST", path, files=files, params=clean_params or None)
        return resp.json()

    async def put(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        clean_params = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("PUT", path, json=body, params=clean_params or None)
        return resp.json()

    async def delete(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        resp = await self._request("DELETE", path, params=clean or None)
        # DELETE endpoints may return empty body
        if resp.status_code == 204 or not resp.content:
            return {"success": True}
        try:
            return resp.json()
        except Exception:
            return {"success": True}

    async def get_raw(self, path: str, **params: Any) -> httpx.Response:
        """Return the raw response (for binary downloads)."""
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=clean or None)
