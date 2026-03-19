"""Tests for the MCP server — all 24 tool handlers, helpers, and tool registration."""

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from koncile_mcp.server import handle_tool, _text, _require, TOOLS, create_server
from koncile_mcp.client import KoncileClient
from koncile_mcp.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return Config(api_url="http://test-api.local", api_key="sk-test", request_timeout=10)


@pytest.fixture
def mock_client():
    """A KoncileClient with all methods mocked."""
    client = AsyncMock(spec=KoncileClient)
    return client


# ---------------------------------------------------------------------------
# Helpers: _text, _require
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_text_wraps_dict(self):
        result = _text({"id": 1, "name": "test"})
        assert len(result) == 1
        assert result[0].type == "text"
        parsed = json.loads(result[0].text)
        assert parsed == {"id": 1, "name": "test"}

    def test_text_wraps_list(self):
        result = _text([1, 2, 3])
        parsed = json.loads(result[0].text)
        assert parsed == [1, 2, 3]

    def test_text_wraps_string(self):
        result = _text("hello")
        parsed = json.loads(result[0].text)
        assert parsed == "hello"

    def test_require_passes_when_present(self):
        _require({"a": 1, "b": "x"}, "a", "b")  # should not raise

    def test_require_raises_on_missing_key(self):
        with pytest.raises(ValueError, match="Missing required parameters: b"):
            _require({"a": 1}, "a", "b")

    def test_require_raises_on_none_value(self):
        with pytest.raises(ValueError, match="Missing required parameters: a"):
            _require({"a": None}, "a")

    def test_require_raises_multiple_missing(self):
        with pytest.raises(ValueError, match="x, y"):
            _require({}, "x", "y")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_tool_count(self):
        assert len(TOOLS) == 24

    def test_all_tools_have_unique_names(self):
        names = [t.name for t in TOOLS]
        assert len(names) == len(set(names))

    def test_all_tools_have_descriptions(self):
        for tool in TOOLS:
            assert tool.description, f"Tool {tool.name} has no description"

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert tool.inputSchema is not None
            assert tool.inputSchema["type"] == "object"

    def test_expected_tool_names(self):
        names = {t.name for t in TOOLS}
        expected = {
            "upload_file", "get_task_status", "list_documents", "get_document_data",
            "list_folders", "get_folder", "create_folder", "update_folder",
            "delete_folder", "get_template", "create_template", "update_template",
            "delete_template", "get_field", "create_field", "update_field",
            "delete_field", "get_instruction", "create_instruction", "update_instruction",
            "delete_instruction", "check_api_key", "download_file", "delete_document",
        }
        assert names == expected

    def test_create_server_returns_server(self, config):
        server = create_server(config)
        assert server is not None


# ---------------------------------------------------------------------------
# P0: Core document workflow
# ---------------------------------------------------------------------------

class TestUploadFile:
    @pytest.mark.asyncio
    async def test_basic_upload(self, mock_client):
        mock_client.post_file.return_value = {"task_ids": ["t-1"]}
        b64 = base64.b64encode(b"pdf-data").decode()
        result = await handle_tool(mock_client, "upload_file", {
            "file_content": b64, "file_name": "invoice.pdf",
        })
        parsed = json.loads(result[0].text)
        assert parsed["task_ids"] == ["t-1"]
        mock_client.post_file.assert_called_once_with(
            "/v1/upload_file",
            file_content_b64=b64,
            file_name="invoice.pdf",
            folder_id=None,
            template_id=None,
            metadata=None,
        )

    @pytest.mark.asyncio
    async def test_upload_with_optional_params(self, mock_client):
        mock_client.post_file.return_value = {"task_ids": ["t-2"]}
        b64 = base64.b64encode(b"data").decode()
        await handle_tool(mock_client, "upload_file", {
            "file_content": b64, "file_name": "f.pdf",
            "folder_id": 10, "template_id": 20, "metadata": "test",
        })
        mock_client.post_file.assert_called_once_with(
            "/v1/upload_file",
            file_content_b64=b64,
            file_name="f.pdf",
            folder_id=10,
            template_id=20,
            metadata="test",
        )

    @pytest.mark.asyncio
    async def test_upload_missing_file_content(self, mock_client):
        with pytest.raises(ValueError, match="file_content"):
            await handle_tool(mock_client, "upload_file", {"file_name": "f.pdf"})

    @pytest.mark.asyncio
    async def test_upload_missing_file_name(self, mock_client):
        with pytest.raises(ValueError, match="file_name"):
            await handle_tool(mock_client, "upload_file", {"file_content": "abc"})


class TestGetTaskStatus:
    @pytest.mark.asyncio
    async def test_pending_status(self, mock_client):
        mock_client.get.return_value = {"status": "PENDING", "status_message": "Queued"}
        result = await handle_tool(mock_client, "get_task_status", {"task_id": "t-1"})
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "PENDING"
        mock_client.get.assert_called_once_with("/v1/fetch_tasks_results", task_id="t-1")

    @pytest.mark.asyncio
    async def test_done_status_with_data(self, mock_client):
        mock_client.get.return_value = {
            "status": "DONE", "status_message": "Complete",
            "document_id": 42,
            "General_fields": {"vendor": "Acme"},
            "Line_fields": {"lines": []},
        }
        result = await handle_tool(mock_client, "get_task_status", {"task_id": "t-1"})
        parsed = json.loads(result[0].text)
        assert parsed["document_id"] == 42
        assert parsed["General_fields"]["vendor"] == "Acme"

    @pytest.mark.asyncio
    async def test_missing_task_id(self, mock_client):
        with pytest.raises(ValueError, match="task_id"):
            await handle_tool(mock_client, "get_task_status", {})


class TestListDocuments:
    @pytest.mark.asyncio
    async def test_no_filters(self, mock_client):
        mock_client.get.return_value = [1, 2, 3]
        result = await handle_tool(mock_client, "list_documents", {})
        parsed = json.loads(result[0].text)
        assert parsed == [1, 2, 3]
        mock_client.get.assert_called_once_with(
            "/v1/fetch_documents", start_date=None, end_date=None,
        )

    @pytest.mark.asyncio
    async def test_with_date_filters(self, mock_client):
        mock_client.get.return_value = [5, 6]
        await handle_tool(mock_client, "list_documents", {
            "start_date": "2024-01-01", "end_date": "2024-12-31",
        })
        mock_client.get.assert_called_once_with(
            "/v1/fetch_documents", start_date="2024-01-01", end_date="2024-12-31",
        )


class TestGetDocumentData:
    @pytest.mark.asyncio
    async def test_returns_extracted_data(self, mock_client):
        mock_client.get.return_value = {
            "General_fields": {"total": "100.00"},
            "Line_fields": {"lines": [{"desc": "Item 1"}]},
        }
        result = await handle_tool(mock_client, "get_document_data", {"document_id": 42})
        parsed = json.loads(result[0].text)
        assert parsed["General_fields"]["total"] == "100.00"
        mock_client.get.assert_called_once_with("/v1/fetch_document_data", document_id=42)

    @pytest.mark.asyncio
    async def test_missing_document_id(self, mock_client):
        with pytest.raises(ValueError, match="document_id"):
            await handle_tool(mock_client, "get_document_data", {})


class TestListFolders:
    @pytest.mark.asyncio
    async def test_returns_folders(self, mock_client):
        mock_client.get.return_value = {"folders": [{"id": 1, "name": "Invoices"}]}
        result = await handle_tool(mock_client, "list_folders", {})
        parsed = json.loads(result[0].text)
        assert parsed["folders"][0]["name"] == "Invoices"
        mock_client.get.assert_called_once_with("/v1/fetch_all_folders")


# ---------------------------------------------------------------------------
# P1: Folders
# ---------------------------------------------------------------------------

class TestGetFolder:
    @pytest.mark.asyncio
    async def test_get_folder(self, mock_client):
        mock_client.get.return_value = {"id": 1, "name": "F", "templates": []}
        result = await handle_tool(mock_client, "get_folder", {"folder_id": 1})
        parsed = json.loads(result[0].text)
        assert parsed["id"] == 1
        mock_client.get.assert_called_once_with("/v1/fetch_folder", folder_id=1)

    @pytest.mark.asyncio
    async def test_missing_folder_id(self, mock_client):
        with pytest.raises(ValueError, match="folder_id"):
            await handle_tool(mock_client, "get_folder", {})


class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_create_with_name(self, mock_client):
        mock_client.post_json.return_value = {"id": 5, "name": "New"}
        result = await handle_tool(mock_client, "create_folder", {"name": "New"})
        parsed = json.loads(result[0].text)
        assert parsed["id"] == 5
        mock_client.post_json.assert_called_once_with("/v1/create_folder", {"name": "New"})

    @pytest.mark.asyncio
    async def test_create_with_desc(self, mock_client):
        mock_client.post_json.return_value = {"id": 5, "name": "New", "desc": "A folder"}
        await handle_tool(mock_client, "create_folder", {"name": "New", "desc": "A folder"})
        mock_client.post_json.assert_called_once_with(
            "/v1/create_folder", {"name": "New", "desc": "A folder"}
        )

    @pytest.mark.asyncio
    async def test_missing_name(self, mock_client):
        with pytest.raises(ValueError, match="name"):
            await handle_tool(mock_client, "create_folder", {})


class TestUpdateFolder:
    @pytest.mark.asyncio
    async def test_update_name(self, mock_client):
        mock_client.put.return_value = {"id": 1, "name": "Renamed"}
        result = await handle_tool(mock_client, "update_folder", {"folder_id": 1, "name": "Renamed"})
        parsed = json.loads(result[0].text)
        assert parsed["name"] == "Renamed"
        mock_client.put.assert_called_once_with(
            "/v1/update_folder", {"name": "Renamed"}, folder_id=1,
        )

    @pytest.mark.asyncio
    async def test_update_desc_only(self, mock_client):
        mock_client.put.return_value = {"id": 1, "desc": "New desc"}
        await handle_tool(mock_client, "update_folder", {"folder_id": 1, "desc": "New desc"})
        mock_client.put.assert_called_once_with(
            "/v1/update_folder", {"desc": "New desc"}, folder_id=1,
        )

    @pytest.mark.asyncio
    async def test_missing_folder_id(self, mock_client):
        with pytest.raises(ValueError, match="folder_id"):
            await handle_tool(mock_client, "update_folder", {"name": "X"})


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_delete_without_override(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_folder", {"folder_id": 1})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_folder", folder_id=1, override=False,
        )

    @pytest.mark.asyncio
    async def test_delete_with_override(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_folder", {"folder_id": 1, "override": True})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_folder", folder_id=1, override=True,
        )

    @pytest.mark.asyncio
    async def test_missing_folder_id(self, mock_client):
        with pytest.raises(ValueError, match="folder_id"):
            await handle_tool(mock_client, "delete_folder", {})


# ---------------------------------------------------------------------------
# P1: Templates
# ---------------------------------------------------------------------------

class TestGetTemplate:
    @pytest.mark.asyncio
    async def test_get_template(self, mock_client):
        mock_client.get.return_value = {
            "id": 10, "name": "Invoice", "folder_id": 1,
            "field_ids": [1, 2], "instruction_ids": [3],
        }
        result = await handle_tool(mock_client, "get_template", {"template_id": 10})
        parsed = json.loads(result[0].text)
        assert parsed["field_ids"] == [1, 2]
        mock_client.get.assert_called_once_with("/v1/fetch_template", template_id=10)

    @pytest.mark.asyncio
    async def test_missing_template_id(self, mock_client):
        with pytest.raises(ValueError, match="template_id"):
            await handle_tool(mock_client, "get_template", {})


class TestCreateTemplate:
    @pytest.mark.asyncio
    async def test_create_basic(self, mock_client):
        mock_client.post_json.return_value = {"id": 10, "name": "T"}
        await handle_tool(mock_client, "create_template", {"folder_id": 1, "name": "T"})
        mock_client.post_json.assert_called_once_with(
            "/v1/create_template", {"folder_id": 1, "name": "T"}, template_id=None,
        )

    @pytest.mark.asyncio
    async def test_create_with_duplicate(self, mock_client):
        mock_client.post_json.return_value = {"id": 11}
        await handle_tool(mock_client, "create_template", {
            "folder_id": 1, "name": "T", "duplicate_from": 5,
        })
        mock_client.post_json.assert_called_once_with(
            "/v1/create_template", {"folder_id": 1, "name": "T"}, template_id=5,
        )

    @pytest.mark.asyncio
    async def test_create_with_desc(self, mock_client):
        mock_client.post_json.return_value = {"id": 11}
        await handle_tool(mock_client, "create_template", {
            "folder_id": 1, "name": "T", "desc": "A template",
        })
        mock_client.post_json.assert_called_once_with(
            "/v1/create_template",
            {"folder_id": 1, "name": "T", "desc": "A template"},
            template_id=None,
        )

    @pytest.mark.asyncio
    async def test_missing_folder_id(self, mock_client):
        with pytest.raises(ValueError, match="folder_id"):
            await handle_tool(mock_client, "create_template", {"name": "T"})

    @pytest.mark.asyncio
    async def test_missing_name(self, mock_client):
        with pytest.raises(ValueError, match="name"):
            await handle_tool(mock_client, "create_template", {"folder_id": 1})


class TestUpdateTemplate:
    @pytest.mark.asyncio
    async def test_update(self, mock_client):
        mock_client.put.return_value = {"id": 10, "name": "Updated"}
        await handle_tool(mock_client, "update_template", {"template_id": 10, "name": "Updated"})
        mock_client.put.assert_called_once_with(
            "/v1/update_template", {"name": "Updated"}, template_id=10,
        )

    @pytest.mark.asyncio
    async def test_missing_template_id(self, mock_client):
        with pytest.raises(ValueError, match="template_id"):
            await handle_tool(mock_client, "update_template", {"name": "X"})


class TestDeleteTemplate:
    @pytest.mark.asyncio
    async def test_delete(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_template", {"template_id": 10})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_template", template_id=10, override=False,
        )

    @pytest.mark.asyncio
    async def test_delete_with_override(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_template", {"template_id": 10, "override": True})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_template", template_id=10, override=True,
        )

    @pytest.mark.asyncio
    async def test_missing_template_id(self, mock_client):
        with pytest.raises(ValueError, match="template_id"):
            await handle_tool(mock_client, "delete_template", {})


# ---------------------------------------------------------------------------
# P1: Fields
# ---------------------------------------------------------------------------

class TestGetField:
    @pytest.mark.asyncio
    async def test_get_field(self, mock_client):
        mock_client.get.return_value = {
            "id": 1, "name": "Total", "type": "General fields",
            "format": "number", "template_id": 10,
        }
        result = await handle_tool(mock_client, "get_field", {"field_id": 1})
        parsed = json.loads(result[0].text)
        assert parsed["name"] == "Total"
        mock_client.get.assert_called_once_with("/v1/fetch_field", field_id=1)

    @pytest.mark.asyncio
    async def test_missing_field_id(self, mock_client):
        with pytest.raises(ValueError, match="field_id"):
            await handle_tool(mock_client, "get_field", {})


class TestCreateField:
    @pytest.mark.asyncio
    async def test_create_required_only(self, mock_client):
        mock_client.post_json.return_value = {"id": 1, "name": "Total"}
        await handle_tool(mock_client, "create_field", {
            "template_id": 10, "name": "Total", "type": "General fields", "format": "number",
        })
        mock_client.post_json.assert_called_once_with("/v1/create_field", {
            "template_id": 10, "name": "Total", "type": "General fields", "format": "number",
        })

    @pytest.mark.asyncio
    async def test_create_with_optional_params(self, mock_client):
        mock_client.post_json.return_value = {"id": 2}
        await handle_tool(mock_client, "create_field", {
            "template_id": 10, "name": "Desc", "type": "Line fields",
            "format": "string", "desc": "Description field", "position": 3,
        })
        mock_client.post_json.assert_called_once_with("/v1/create_field", {
            "template_id": 10, "name": "Desc", "type": "Line fields",
            "format": "string", "desc": "Description field", "position": 3,
        })

    @pytest.mark.asyncio
    async def test_missing_required_params(self, mock_client):
        with pytest.raises(ValueError, match="template_id"):
            await handle_tool(mock_client, "create_field", {
                "name": "X", "type": "General fields", "format": "string",
            })

    @pytest.mark.asyncio
    async def test_missing_format(self, mock_client):
        with pytest.raises(ValueError, match="format"):
            await handle_tool(mock_client, "create_field", {
                "template_id": 10, "name": "X", "type": "General fields",
            })


class TestUpdateField:
    @pytest.mark.asyncio
    async def test_update_name(self, mock_client):
        mock_client.put.return_value = {"id": 1, "name": "New Name"}
        await handle_tool(mock_client, "update_field", {"field_id": 1, "name": "New Name"})
        mock_client.put.assert_called_once_with(
            "/v1/update_field", {"name": "New Name"}, field_id=1,
        )

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, mock_client):
        mock_client.put.return_value = {"id": 1}
        await handle_tool(mock_client, "update_field", {
            "field_id": 1, "name": "X", "type": "Line fields", "format": "date", "desc": "D",
        })
        mock_client.put.assert_called_once_with(
            "/v1/update_field",
            {"name": "X", "type": "Line fields", "format": "date", "desc": "D"},
            field_id=1,
        )

    @pytest.mark.asyncio
    async def test_missing_field_id(self, mock_client):
        with pytest.raises(ValueError, match="field_id"):
            await handle_tool(mock_client, "update_field", {"name": "X"})


class TestDeleteField:
    @pytest.mark.asyncio
    async def test_delete(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_field", {"field_id": 1})
        mock_client.delete.assert_called_once_with("/v1/delete_field", field_id=1)

    @pytest.mark.asyncio
    async def test_missing_field_id(self, mock_client):
        with pytest.raises(ValueError, match="field_id"):
            await handle_tool(mock_client, "delete_field", {})


# ---------------------------------------------------------------------------
# P1: Instructions
# ---------------------------------------------------------------------------

class TestGetInstruction:
    @pytest.mark.asyncio
    async def test_get_instruction(self, mock_client):
        mock_client.get.return_value = {
            "id": 3, "content": "Extract totals", "type": "General fields", "template_id": 10,
        }
        result = await handle_tool(mock_client, "get_instruction", {"instruction_id": 3})
        parsed = json.loads(result[0].text)
        assert parsed["content"] == "Extract totals"
        mock_client.get.assert_called_once_with("/v1/fetch_instruction", instruction_id=3)

    @pytest.mark.asyncio
    async def test_missing_instruction_id(self, mock_client):
        with pytest.raises(ValueError, match="instruction_id"):
            await handle_tool(mock_client, "get_instruction", {})


class TestCreateInstruction:
    @pytest.mark.asyncio
    async def test_create(self, mock_client):
        mock_client.post_json.return_value = {"id": 3, "content": "Do X", "type": "General fields"}
        await handle_tool(mock_client, "create_instruction", {
            "template_id": 10, "content": "Do X", "type": "General fields",
        })
        mock_client.post_json.assert_called_once_with("/v1/create_instruction", {
            "template_id": 10, "content": "Do X", "type": "General fields",
        })

    @pytest.mark.asyncio
    async def test_missing_content(self, mock_client):
        with pytest.raises(ValueError, match="content"):
            await handle_tool(mock_client, "create_instruction", {
                "template_id": 10, "type": "General fields",
            })

    @pytest.mark.asyncio
    async def test_missing_type(self, mock_client):
        with pytest.raises(ValueError, match="type"):
            await handle_tool(mock_client, "create_instruction", {
                "template_id": 10, "content": "Do X",
            })

    @pytest.mark.asyncio
    async def test_missing_template_id(self, mock_client):
        with pytest.raises(ValueError, match="template_id"):
            await handle_tool(mock_client, "create_instruction", {
                "content": "Do X", "type": "General fields",
            })


class TestUpdateInstruction:
    @pytest.mark.asyncio
    async def test_update_content(self, mock_client):
        mock_client.put.return_value = {"id": 3, "content": "Updated"}
        await handle_tool(mock_client, "update_instruction", {
            "instruction_id": 3, "content": "Updated",
        })
        mock_client.put.assert_called_once_with(
            "/v1/update_instruction", {"content": "Updated"}, instruction_id=3,
        )

    @pytest.mark.asyncio
    async def test_update_type(self, mock_client):
        mock_client.put.return_value = {"id": 3}
        await handle_tool(mock_client, "update_instruction", {
            "instruction_id": 3, "type": "Line fields",
        })
        mock_client.put.assert_called_once_with(
            "/v1/update_instruction", {"type": "Line fields"}, instruction_id=3,
        )

    @pytest.mark.asyncio
    async def test_update_both(self, mock_client):
        mock_client.put.return_value = {"id": 3}
        await handle_tool(mock_client, "update_instruction", {
            "instruction_id": 3, "content": "New", "type": "Line fields",
        })
        mock_client.put.assert_called_once_with(
            "/v1/update_instruction",
            {"content": "New", "type": "Line fields"},
            instruction_id=3,
        )

    @pytest.mark.asyncio
    async def test_missing_instruction_id(self, mock_client):
        with pytest.raises(ValueError, match="instruction_id"):
            await handle_tool(mock_client, "update_instruction", {"content": "X"})


class TestDeleteInstruction:
    @pytest.mark.asyncio
    async def test_delete(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_instruction", {"instruction_id": 3})
        mock_client.delete.assert_called_once_with("/v1/delete_instruction", instruction_id=3)

    @pytest.mark.asyncio
    async def test_missing_instruction_id(self, mock_client):
        with pytest.raises(ValueError, match="instruction_id"):
            await handle_tool(mock_client, "delete_instruction", {})


# ---------------------------------------------------------------------------
# P2: Utilities
# ---------------------------------------------------------------------------

class TestCheckApiKey:
    @pytest.mark.asyncio
    async def test_check_success(self, mock_client):
        mock_client.post_json.return_value = {"success": True}
        result = await handle_tool(mock_client, "check_api_key", {})
        parsed = json.loads(result[0].text)
        assert parsed["success"] is True
        mock_client.post_json.assert_called_once_with("/v1/check_api_key", {})


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_download_by_document_id(self, mock_client):
        mock_resp = AsyncMock()
        mock_resp.content = b"PDF-binary-content"
        mock_resp.headers = {"X-Filename": "invoice.pdf"}
        mock_client.get_raw.return_value = mock_resp

        result = await handle_tool(mock_client, "download_file", {"document_id": 42})
        parsed = json.loads(result[0].text)
        assert parsed["filename"] == "invoice.pdf"
        decoded = base64.b64decode(parsed["content_base64"])
        assert decoded == b"PDF-binary-content"
        mock_client.get_raw.assert_called_once_with(
            "/v1/fetch_file", document_id=42, task_id=None,
        )

    @pytest.mark.asyncio
    async def test_download_by_task_id(self, mock_client):
        mock_resp = AsyncMock()
        mock_resp.content = b"data"
        mock_resp.headers = {"X-Filename": "doc.pdf"}
        mock_client.get_raw.return_value = mock_resp

        await handle_tool(mock_client, "download_file", {"task_id": "t-1"})
        mock_client.get_raw.assert_called_once_with(
            "/v1/fetch_file", document_id=None, task_id="t-1",
        )

    @pytest.mark.asyncio
    async def test_download_missing_header_uses_fallback(self, mock_client):
        mock_resp = AsyncMock()
        mock_resp.content = b"data"
        mock_resp.headers = {}
        mock_client.get_raw.return_value = mock_resp

        result = await handle_tool(mock_client, "download_file", {"document_id": 1})
        parsed = json.loads(result[0].text)
        assert parsed["filename"] == "file"

    @pytest.mark.asyncio
    async def test_download_missing_both_ids(self, mock_client):
        with pytest.raises(ValueError, match="document_id or task_id"):
            await handle_tool(mock_client, "download_file", {})


class TestDeleteDocument:
    @pytest.mark.asyncio
    async def test_delete_doc_only(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_document", {"doc_id": 42})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_doc", doc_id=42, delete_file=False,
        )

    @pytest.mark.asyncio
    async def test_delete_doc_and_file(self, mock_client):
        mock_client.delete.return_value = {"success": True}
        await handle_tool(mock_client, "delete_document", {"doc_id": 42, "delete_file": True})
        mock_client.delete.assert_called_once_with(
            "/v1/delete_doc", doc_id=42, delete_file=True,
        )

    @pytest.mark.asyncio
    async def test_missing_doc_id(self, mock_client):
        with pytest.raises(ValueError, match="doc_id"):
            await handle_tool(mock_client, "delete_document", {})


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self, mock_client):
        with pytest.raises(ValueError, match="Unknown tool: nonexistent"):
            await handle_tool(mock_client, "nonexistent", {})

    @pytest.mark.asyncio
    async def test_empty_args_dict(self, mock_client):
        """Tools with no required params should work with empty args."""
        mock_client.get.return_value = {"folders": []}
        result = await handle_tool(mock_client, "list_folders", {})
        parsed = json.loads(result[0].text)
        assert parsed == {"folders": []}

    @pytest.mark.asyncio
    async def test_handle_tool_propagates_client_error(self, mock_client):
        """RuntimeError from client should propagate up."""
        mock_client.get.side_effect = RuntimeError("Not found: Folder not found")
        with pytest.raises(RuntimeError, match="Not found"):
            await handle_tool(mock_client, "get_folder", {"folder_id": 999})
