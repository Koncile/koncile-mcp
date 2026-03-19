"""End-to-end tests — MCP client ↔ koncile-mcp server ↔ mock Koncile API.

Each test:
1. Starts a mock HTTP server (threading) that simulates Koncile API responses
2. Launches `koncile-mcp` as a subprocess via the MCP stdio client
3. Sends MCP tool calls and asserts the results
"""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


# ---------------------------------------------------------------------------
# Mock Koncile API
# ---------------------------------------------------------------------------

class MockKoncileHandler(BaseHTTPRequestHandler):
    """Minimal mock of the Koncile public API endpoints."""

    def log_message(self, format, *args):
        """Suppress request logs in test output."""
        pass

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, content: bytes, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("X-Filename", filename)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _query_params(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    def _path(self) -> str:
        return urlparse(self.path).path.rstrip("/")

    # -- Authorization check -----------------------------------------------

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_json({"detail": "Not authenticated"}, 401)
            return False
        return True

    # -- GET ----------------------------------------------------------------

    def do_GET(self):
        if not self._check_auth():
            return
        path = self._path()
        params = self._query_params()

        if path == "/v1/fetch_all_folders":
            self._send_json({
                "folders": [
                    {"id": 1, "name": "Invoices", "desc": "Invoice folder",
                     "templates": [{"id": 10, "name": "Standard Invoice", "desc": None}]},
                    {"id": 2, "name": "Receipts", "desc": None, "templates": []},
                ]
            })

        elif path == "/v1/fetch_folder":
            folder_id = int(params.get("folder_id", 0))
            if folder_id == 1:
                self._send_json({
                    "id": 1, "name": "Invoices", "desc": "Invoice folder",
                    "templates": [{"id": 10, "name": "Standard Invoice", "desc": None}],
                })
            else:
                self._send_json({"detail": "Folder not found"}, 404)

        elif path == "/v1/fetch_template":
            template_id = int(params.get("template_id", 0))
            if template_id == 10:
                self._send_json({
                    "id": 10, "name": "Standard Invoice", "desc": None,
                    "folder_id": 1, "field_ids": [100, 101], "instruction_ids": [200],
                })
            else:
                self._send_json({"detail": "Template not found"}, 404)

        elif path == "/v1/fetch_field":
            field_id = int(params.get("field_id", 0))
            if field_id == 100:
                self._send_json({
                    "id": 100, "name": "Total", "type": "General fields",
                    "format": "number", "desc": "Invoice total", "position": 1,
                    "template_id": 10,
                })
            else:
                self._send_json({"detail": "Field not found"}, 404)

        elif path == "/v1/fetch_instruction":
            instruction_id = int(params.get("instruction_id", 0))
            if instruction_id == 200:
                self._send_json({
                    "id": 200, "content": "Extract the total amount",
                    "type": "General fields", "template_id": 10,
                })
            else:
                self._send_json({"detail": "Instruction not found"}, 404)

        elif path == "/v1/fetch_documents":
            self._send_json([1001, 1002, 1003])

        elif path == "/v1/fetch_document_data":
            doc_id = int(params.get("document_id", 0))
            if doc_id == 1001:
                self._send_json({
                    "General_fields": {"vendor": "Acme Corp", "total": "1234.56", "date": "2024-03-15"},
                    "Line_fields": {
                        "lines": [
                            {"description": "Widget A", "quantity": "10", "unit_price": "100.00"},
                            {"description": "Widget B", "quantity": "5", "unit_price": "46.91"},
                        ]
                    },
                })
            else:
                self._send_json({"detail": "Document not found"}, 404)

        elif path == "/v1/fetch_tasks_results":
            task_id = params.get("task_id", "")
            if task_id == "task-done":
                self._send_json({
                    "status": "DONE", "status_message": "Extraction complete",
                    "task_id": "task-done", "document_id": 1001, "document_name": "invoice.pdf",
                    "General_fields": {"vendor": "Acme Corp", "total": "1234.56"},
                    "Line_fields": {"lines": []},
                })
            elif task_id == "task-pending":
                self._send_json({
                    "status": "PENDING", "status_message": "Queued for processing",
                    "task_id": "task-pending",
                })
            else:
                self._send_json({"detail": "Task not found"}, 404)

        elif path == "/v1/fetch_file":
            doc_id = params.get("document_id")
            task_id = params.get("task_id")
            if doc_id == "1001" or task_id == "task-done":
                self._send_file(b"%PDF-1.4 fake pdf content", "invoice.pdf")
            else:
                self._send_json({"detail": "Document not found"}, 404)

        else:
            self._send_json({"detail": "Not found"}, 404)

    # -- POST ---------------------------------------------------------------

    def do_POST(self):
        if not self._check_auth():
            return
        path = self._path()
        params = self._query_params()
        body_bytes = self._read_body()

        if path == "/v1/check_api_key":
            self._send_json({"success": True})

        elif path == "/v1/upload_file":
            # multipart form data — just return mock task IDs
            self._send_json({
                "task_ids": ["task-new-001"],
                "message": "1 file(s) uploaded successfully",
            })

        elif path == "/v1/create_folder":
            data = json.loads(body_bytes)
            self._send_json({
                "id": 99, "name": data.get("name", ""),
                "desc": data.get("desc"), "templates": [],
            })

        elif path == "/v1/create_template":
            data = json.loads(body_bytes)
            self._send_json({
                "id": 99, "name": data.get("name", ""),
                "desc": data.get("desc"), "folder_id": data.get("folder_id"),
                "field_ids": [], "instruction_ids": [],
            })

        elif path == "/v1/create_field":
            data = json.loads(body_bytes)
            self._send_json({
                "id": 999, "name": data.get("name", ""),
                "type": data.get("type"), "format": data.get("format"),
                "desc": data.get("desc"), "position": data.get("position"),
                "template_id": data.get("template_id"),
            })

        elif path == "/v1/create_instruction":
            data = json.loads(body_bytes)
            self._send_json({
                "id": 999, "content": data.get("content", ""),
                "type": data.get("type"), "template_id": data.get("template_id"),
            })

        else:
            self._send_json({"detail": "Not found"}, 404)

    # -- PUT ----------------------------------------------------------------

    def do_PUT(self):
        if not self._check_auth():
            return
        path = self._path()
        params = self._query_params()
        data = json.loads(self._read_body())

        if path == "/v1/update_folder":
            folder_id = int(params.get("folder_id", 0))
            self._send_json({
                "id": folder_id,
                "name": data.get("name", "Invoices"),
                "desc": data.get("desc"),
                "templates": [],
            })

        elif path == "/v1/update_template":
            template_id = int(params.get("template_id", 0))
            self._send_json({
                "id": template_id,
                "name": data.get("name", "Template"),
                "desc": data.get("desc"),
                "folder_id": 1, "field_ids": [], "instruction_ids": [],
            })

        elif path == "/v1/update_field":
            field_id = int(params.get("field_id", 0))
            self._send_json({
                "id": field_id, "name": data.get("name", "Field"),
                "type": data.get("type", "General fields"),
                "format": data.get("format", "string"),
                "desc": data.get("desc"), "position": None,
                "template_id": 10,
            })

        elif path == "/v1/update_instruction":
            instr_id = int(params.get("instruction_id", 0))
            self._send_json({
                "id": instr_id,
                "content": data.get("content", "Instruction"),
                "type": data.get("type", "General fields"),
                "template_id": 10,
            })

        else:
            self._send_json({"detail": "Not found"}, 404)

    # -- DELETE -------------------------------------------------------------

    def do_DELETE(self):
        if not self._check_auth():
            return
        path = self._path()
        params = self._query_params()

        if path in ("/v1/delete_folder", "/v1/delete_template",
                     "/v1/delete_field", "/v1/delete_instruction", "/v1/delete_doc"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_json({"detail": "Not found"}, 404)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_api_url():
    """Start a mock Koncile API server for the test module, return its URL."""
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), MockKoncileHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def server_params(mock_api_url):
    """StdioServerParameters for launching koncile-mcp."""
    return StdioServerParameters(
        command="koncile-mcp",
        env={
            **os.environ,
            "KONCILE_API_URL": mock_api_url,
            "KONCILE_API_KEY": "test-e2e-key",
            "KONCILE_REQUEST_TIMEOUT": "10",
        },
    )


def _parse(result) -> dict | list | str:
    """Parse the text content from a CallToolResult."""
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Each test creates its own MCP session to keep tests independent.
# The mock HTTP server is shared (module-scoped) for speed.


class TestE2ECoreWorkflow:
    """P0 tools: upload, task status, documents, folders."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_24(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                names = {t.name for t in tools_result.tools}
                assert len(names) == 24
                assert "upload_file" in names
                assert "check_api_key" in names
                assert "delete_document" in names

    @pytest.mark.asyncio
    async def test_check_api_key(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("check_api_key", {})
                data = _parse(result)
                assert data["success"] is True

    @pytest.mark.asyncio
    async def test_list_folders(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_folders", {})
                data = _parse(result)
                assert len(data["folders"]) == 2
                assert data["folders"][0]["name"] == "Invoices"
                assert data["folders"][0]["templates"][0]["name"] == "Standard Invoice"

    @pytest.mark.asyncio
    async def test_list_documents(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_documents", {})
                data = _parse(result)
                assert data == [1001, 1002, 1003]

    @pytest.mark.asyncio
    async def test_list_documents_with_filters(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_documents", {
                    "start_date": "2024-01-01", "end_date": "2024-12-31",
                })
                data = _parse(result)
                assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_document_data(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_document_data", {"document_id": 1001})
                data = _parse(result)
                assert data["General_fields"]["vendor"] == "Acme Corp"
                assert data["General_fields"]["total"] == "1234.56"
                assert len(data["Line_fields"]["lines"]) == 2

    @pytest.mark.asyncio
    async def test_get_task_status_done(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_task_status", {"task_id": "task-done"})
                data = _parse(result)
                assert data["status"] == "DONE"
                assert data["document_id"] == 1001
                assert data["General_fields"]["vendor"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_get_task_status_pending(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_task_status", {"task_id": "task-pending"})
                data = _parse(result)
                assert data["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_upload_file(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                file_content = base64.b64encode(b"%PDF-1.4 test content").decode()
                result = await session.call_tool("upload_file", {
                    "file_content": file_content,
                    "file_name": "test_invoice.pdf",
                })
                data = _parse(result)
                assert "task_ids" in data
                assert data["task_ids"] == ["task-new-001"]

    @pytest.mark.asyncio
    async def test_upload_file_with_routing(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                file_content = base64.b64encode(b"data").decode()
                result = await session.call_tool("upload_file", {
                    "file_content": file_content,
                    "file_name": "invoice.pdf",
                    "folder_id": 1,
                    "template_id": 10,
                })
                data = _parse(result)
                assert "task_ids" in data


class TestE2EFolderCRUD:
    """Folder management tools."""

    @pytest.mark.asyncio
    async def test_get_folder(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_folder", {"folder_id": 1})
                data = _parse(result)
                assert data["id"] == 1
                assert data["name"] == "Invoices"
                assert len(data["templates"]) == 1

    @pytest.mark.asyncio
    async def test_get_folder_not_found(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_folder", {"folder_id": 9999})
                # MCP returns isError when the tool handler raises
                assert result.isError

    @pytest.mark.asyncio
    async def test_create_folder(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("create_folder", {
                    "name": "New Folder", "desc": "Test folder",
                })
                data = _parse(result)
                assert data["id"] == 99
                assert data["name"] == "New Folder"

    @pytest.mark.asyncio
    async def test_update_folder(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("update_folder", {
                    "folder_id": 1, "name": "Renamed Folder",
                })
                data = _parse(result)
                assert data["id"] == 1
                assert data["name"] == "Renamed Folder"

    @pytest.mark.asyncio
    async def test_delete_folder(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_folder", {
                    "folder_id": 1, "override": True,
                })
                data = _parse(result)
                assert data["success"] is True


class TestE2ETemplateCRUD:
    """Template management tools."""

    @pytest.mark.asyncio
    async def test_get_template(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_template", {"template_id": 10})
                data = _parse(result)
                assert data["id"] == 10
                assert data["name"] == "Standard Invoice"
                assert data["field_ids"] == [100, 101]
                assert data["instruction_ids"] == [200]

    @pytest.mark.asyncio
    async def test_get_template_not_found(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_template", {"template_id": 9999})
                assert result.isError

    @pytest.mark.asyncio
    async def test_create_template(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("create_template", {
                    "folder_id": 1, "name": "New Template", "desc": "For testing",
                })
                data = _parse(result)
                assert data["id"] == 99
                assert data["name"] == "New Template"

    @pytest.mark.asyncio
    async def test_update_template(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("update_template", {
                    "template_id": 10, "name": "Updated Template",
                })
                data = _parse(result)
                assert data["id"] == 10
                assert data["name"] == "Updated Template"

    @pytest.mark.asyncio
    async def test_delete_template(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_template", {"template_id": 10})
                data = _parse(result)
                assert data["success"] is True


class TestE2EFieldCRUD:
    """Field management tools."""

    @pytest.mark.asyncio
    async def test_get_field(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_field", {"field_id": 100})
                data = _parse(result)
                assert data["id"] == 100
                assert data["name"] == "Total"
                assert data["type"] == "General fields"
                assert data["format"] == "number"

    @pytest.mark.asyncio
    async def test_get_field_not_found(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_field", {"field_id": 9999})
                assert result.isError

    @pytest.mark.asyncio
    async def test_create_field(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("create_field", {
                    "template_id": 10, "name": "Vendor",
                    "type": "General fields", "format": "string",
                    "desc": "Vendor name",
                })
                data = _parse(result)
                assert data["id"] == 999
                assert data["name"] == "Vendor"

    @pytest.mark.asyncio
    async def test_update_field(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("update_field", {
                    "field_id": 100, "name": "Grand Total",
                })
                data = _parse(result)
                assert data["id"] == 100
                assert data["name"] == "Grand Total"

    @pytest.mark.asyncio
    async def test_delete_field(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_field", {"field_id": 100})
                data = _parse(result)
                assert data["success"] is True


class TestE2EInstructionCRUD:
    """Instruction management tools."""

    @pytest.mark.asyncio
    async def test_get_instruction(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_instruction", {"instruction_id": 200})
                data = _parse(result)
                assert data["id"] == 200
                assert data["content"] == "Extract the total amount"
                assert data["type"] == "General fields"

    @pytest.mark.asyncio
    async def test_get_instruction_not_found(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_instruction", {"instruction_id": 9999})
                assert result.isError

    @pytest.mark.asyncio
    async def test_create_instruction(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("create_instruction", {
                    "template_id": 10,
                    "content": "Extract line item descriptions",
                    "type": "Line fields",
                })
                data = _parse(result)
                assert data["id"] == 999
                assert data["content"] == "Extract line item descriptions"

    @pytest.mark.asyncio
    async def test_update_instruction(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("update_instruction", {
                    "instruction_id": 200, "content": "Updated instruction",
                })
                data = _parse(result)
                assert data["id"] == 200
                assert data["content"] == "Updated instruction"

    @pytest.mark.asyncio
    async def test_delete_instruction(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_instruction", {"instruction_id": 200})
                data = _parse(result)
                assert data["success"] is True


class TestE2EUtilities:
    """Utility tools: download, delete document."""

    @pytest.mark.asyncio
    async def test_download_file_by_document_id(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("download_file", {"document_id": 1001})
                data = _parse(result)
                assert data["filename"] == "invoice.pdf"
                decoded = base64.b64decode(data["content_base64"])
                assert b"%PDF-1.4" in decoded

    @pytest.mark.asyncio
    async def test_download_file_by_task_id(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("download_file", {"task_id": "task-done"})
                data = _parse(result)
                assert data["filename"] == "invoice.pdf"

    @pytest.mark.asyncio
    async def test_download_file_missing_ids(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("download_file", {})
                assert result.isError

    @pytest.mark.asyncio
    async def test_delete_document(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_document", {"doc_id": 1001})
                data = _parse(result)
                assert data["success"] is True

    @pytest.mark.asyncio
    async def test_delete_document_with_file(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("delete_document", {
                    "doc_id": 1001, "delete_file": True,
                })
                data = _parse(result)
                assert data["success"] is True


class TestE2EErrorHandling:
    """Verify errors propagate correctly through the full stack."""

    @pytest.mark.asyncio
    async def test_missing_required_param(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_task_status", {})
                assert result.isError
                assert "task_id" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_api_404_propagates(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_document_data", {"document_id": 9999})
                assert result.isError
                assert "not found" in result.content[0].text.lower()


class TestE2EFullWorkflow:
    """End-to-end workflow: upload → poll → read data."""

    @pytest.mark.asyncio
    async def test_upload_poll_extract_workflow(self, server_params):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # 1. Upload file
                file_content = base64.b64encode(b"%PDF-1.4 invoice data").decode()
                upload_result = await session.call_tool("upload_file", {
                    "file_content": file_content,
                    "file_name": "workflow_test.pdf",
                    "folder_id": 1,
                    "template_id": 10,
                })
                upload_data = _parse(upload_result)
                assert "task_ids" in upload_data
                task_id = upload_data["task_ids"][0]
                assert task_id == "task-new-001"

                # 2. Check task status (use pre-seeded "task-done" for DONE state)
                status_result = await session.call_tool("get_task_status", {"task_id": "task-done"})
                status_data = _parse(status_result)
                assert status_data["status"] == "DONE"
                document_id = status_data["document_id"]
                assert document_id == 1001

                # 3. Get extracted data
                doc_result = await session.call_tool("get_document_data", {"document_id": document_id})
                doc_data = _parse(doc_result)
                assert doc_data["General_fields"]["vendor"] == "Acme Corp"
                assert doc_data["General_fields"]["total"] == "1234.56"
                assert len(doc_data["Line_fields"]["lines"]) == 2

                # 4. Download the original file
                file_result = await session.call_tool("download_file", {"document_id": document_id})
                file_data = _parse(file_result)
                assert file_data["filename"] == "invoice.pdf"
                decoded = base64.b64decode(file_data["content_base64"])
                assert b"%PDF-1.4" in decoded

    @pytest.mark.asyncio
    async def test_folder_template_field_workflow(self, server_params):
        """Create folder → create template → add field → add instruction → verify."""
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # 1. Create folder
                folder = _parse(await session.call_tool("create_folder", {
                    "name": "E2E Test Folder", "desc": "Created by e2e test",
                }))
                assert folder["name"] == "E2E Test Folder"

                # 2. Create template in folder
                template = _parse(await session.call_tool("create_template", {
                    "folder_id": folder["id"], "name": "E2E Template",
                }))
                assert template["name"] == "E2E Template"

                # 3. Add a field
                field = _parse(await session.call_tool("create_field", {
                    "template_id": template["id"],
                    "name": "Invoice Number",
                    "type": "General fields",
                    "format": "string",
                    "desc": "The invoice number",
                }))
                assert field["name"] == "Invoice Number"

                # 4. Add an instruction
                instruction = _parse(await session.call_tool("create_instruction", {
                    "template_id": template["id"],
                    "content": "Extract the invoice number from the header",
                    "type": "General fields",
                }))
                assert instruction["content"] == "Extract the invoice number from the header"

                # 5. Verify template has the right structure
                fetched = _parse(await session.call_tool("get_template", {"template_id": 10}))
                assert fetched["field_ids"] == [100, 101]
                assert fetched["instruction_ids"] == [200]
