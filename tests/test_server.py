"""Tests for the MCP server — all 24 tool handlers, helpers, and tool registration."""

import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.types import Tool

from koncile_mcp.client import KoncileClient
from koncile_mcp.config import Config
from koncile_mcp.server import (
    TOOLS,
    _require,
    _text,
    create_server,
    handle_tool,
    tools_for,
)

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
        assert len(TOOLS) == 27

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
            "create_upload_links", "register_uploaded_files", "get_fraud_analysis",
        }
        assert names == expected

    def test_create_server_returns_server(self, config):
        server = create_server(config)
        assert server is not None


# ---------------------------------------------------------------------------
# Annotations
#
# These freeze the classification as literals on purpose: reclassifying a tool
# should be a deliberate edit to this file, not something that happens by
# accident. A client that respects the hints prompts the user before running a
# destructive tool, so getting this wrong is either an unnecessary prompt or a
# silent delete.
# ---------------------------------------------------------------------------

READ_ONLY_TOOLS = {
    "get_task_status", "list_documents", "get_document_data", "get_fraud_analysis",
    "list_folders", "get_folder", "get_template", "get_field", "get_instruction",
    "check_api_key", "download_file",
}

DESTRUCTIVE_TOOLS = {
    "update_folder", "delete_folder",
    "update_template", "delete_template",
    "update_field", "delete_field",
    "update_instruction", "delete_instruction",
    "delete_document",
}


class TestToolAnnotations:
    def test_every_tool_has_a_title_and_annotations(self):
        for tool in TOOLS:
            assert tool.title, f"{tool.name} has no title"
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.title == tool.title, tool.name

    def test_read_only_tools_are_marked_explicitly(self):
        # The spec's default for destructiveHint is TRUE, so an unmarked
        # read-only tool reads as destructive to a client.
        actual = {t.name for t in TOOLS if t.annotations.readOnlyHint}
        assert actual == READ_ONLY_TOOLS

    def test_destructive_tools_are_marked(self):
        actual = {
            t.name for t in TOOLS
            if not t.annotations.readOnlyHint and t.annotations.destructiveHint
        }
        assert actual == DESTRUCTIVE_TOOLS

    def test_every_writing_tool_states_whether_it_is_destructive(self):
        for tool in TOOLS:
            if tool.annotations.readOnlyHint:
                continue
            assert tool.annotations.destructiveHint is not None, (
                f"{tool.name} leaves destructiveHint unset, which the spec reads "
                "as destructive"
            )

    def test_nothing_claims_to_reach_outside_the_workspace(self):
        for tool in TOOLS:
            assert tool.annotations.openWorldHint is False, tool.name

    def test_annotation_table_must_cover_every_tool(self):
        # The import-time guard is what stops tool #28 shipping unannotated.
        from koncile_mcp.server import _ANNOTATIONS, _TOOL_SPECS, _annotate

        assert {t.name for t in _TOOL_SPECS} == set(_ANNOTATIONS)
        with pytest.raises(RuntimeError, match="out of sync"):
            _annotate(_TOOL_SPECS + [Tool(name="brand_new", description="x",
                                          inputSchema={"type": "object"})])


class TestHostedToolListing:
    """The advertised schema must match what the transport actually accepts."""

    def test_hosted_listing_drops_file_path(self):
        hosted = {t.name: t for t in tools_for(allow_local_files=False)}
        props = hosted["upload_file"].inputSchema["properties"]
        assert "file_path" not in props
        assert "file_content" in props

    def test_local_listing_keeps_file_path(self):
        local = {t.name: t for t in tools_for(allow_local_files=True)}
        assert "file_path" in local["upload_file"].inputSchema["properties"]

    def test_canonical_tools_are_not_mutated(self):
        # tools_for must not edit TOOLS in place — the hosted listing is built
        # once per request and would otherwise strip the parameter permanently.
        tools_for(allow_local_files=False)
        canonical = {t.name: t for t in TOOLS}
        assert "file_path" in canonical["upload_file"].inputSchema["properties"]


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


# ---------------------------------------------------------------------------
# The presigned upload flow (KON-1055)
# ---------------------------------------------------------------------------

class TestCreateUploadLinks:
    @pytest.mark.asyncio
    async def test_returns_links_and_a_ready_to_run_command(self, mock_client):
        mock_client.post_json.return_value = {
            "files": [{
                "upload_id": 42,
                "filename": "invoice.pdf",
                "upload_url": "https://s3.example/put?sig=abc",
                "expires_in": 900,
            }]
        }
        result = await handle_tool(
            mock_client, "create_upload_links", {"filenames": ["invoice.pdf"]}
        )
        parsed = json.loads(result[0].text)
        link = parsed["upload_links"][0]

        assert link["upload_id"] == 42
        assert link["expires_in_seconds"] == 900
        # The command is the deliverable: in Claude Code or Cursor the assistant
        # runs it itself, elsewhere the person pastes it.
        assert link["put_command"].startswith("curl -X PUT --upload-file")
        assert "https://s3.example/put?sig=abc" in link["put_command"]
        assert parsed["next_step"] == "register_uploaded_files"

    @pytest.mark.asyncio
    async def test_tells_the_model_not_to_fetch_the_url(self, mock_client):
        mock_client.post_json.return_value = {"files": []}
        result = await handle_tool(
            mock_client, "create_upload_links", {"filenames": ["a.pdf"]}
        )
        guidance = json.loads(result[0].text)["instructions_for_the_assistant"]
        # A GET on a presigned PUT URL fails, so a model that "checks the link"
        # reports a broken upload that was never broken.
        assert "Do NOT fetch" in guidance
        assert "register_uploaded_files" in guidance

    @pytest.mark.asyncio
    async def test_routing_is_forwarded(self, mock_client):
        mock_client.post_json.return_value = {"files": []}
        await handle_tool(mock_client, "create_upload_links", {
            "filenames": ["a.pdf"], "folder_id": 1, "template_id": 2,
        })
        mock_client.post_json.assert_called_once_with(
            "/v1/presign_upload",
            {"filenames": ["a.pdf"], "folder_id": 1, "template_id": 2, "metadata": None},
        )

    @pytest.mark.asyncio
    async def test_filenames_required(self, mock_client):
        with pytest.raises(ValueError, match="filenames"):
            await handle_tool(mock_client, "create_upload_links", {})


class TestRegisterUploadedFiles:
    @pytest.mark.asyncio
    async def test_returns_task_ids_and_points_at_polling(self, mock_client):
        mock_client.post_json.return_value = {"task_ids": ["t-9"], "skipped": []}
        result = await handle_tool(
            mock_client, "register_uploaded_files", {"upload_ids": [42]}
        )
        parsed = json.loads(result[0].text)
        assert parsed["task_ids"] == ["t-9"]
        assert parsed["next_step"] == "get_task_status"

    @pytest.mark.asyncio
    async def test_skipped_is_passed_through(self, mock_client):
        # Retrying is safe, and the model needs to see that a handle was skipped
        # rather than assume its extraction started.
        mock_client.post_json.return_value = {
            "task_ids": [], "skipped": [{"upload_id": 42, "reason": "already_dispatched"}],
        }
        result = await handle_tool(
            mock_client, "register_uploaded_files", {"upload_ids": [42]}
        )
        parsed = json.loads(result[0].text)
        assert parsed["skipped"][0]["reason"] == "already_dispatched"

    @pytest.mark.asyncio
    async def test_upload_ids_required(self, mock_client):
        with pytest.raises(ValueError, match="upload_ids"):
            await handle_tool(mock_client, "register_uploaded_files", {})


class TestFilePathCapability:
    """file_path reads a file off the machine the server runs on."""

    @pytest.mark.asyncio
    async def test_refused_by_default(self, mock_client):
        with pytest.raises(ValueError) as exc:
            await handle_tool(
                mock_client, "upload_file", {"file_path": "/Users/someone/invoice.pdf"}
            )
        # The old message named the user's own path from inside the container,
        # which looked plausible and sent people hunting for the wrong problem.
        msg = str(exc.value)
        assert "not supported on the hosted" in msg
        assert "create_upload_links" in msg
        assert "/Users/someone" not in msg
        mock_client.post_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_when_granted(self, mock_client, tmp_path):
        f = tmp_path / "invoice.pdf"
        f.write_bytes(b"%PDF-1.4 hello")
        mock_client.post_file.return_value = {"task_ids": ["t-1"]}

        await handle_tool(
            mock_client, "upload_file", {"file_path": str(f)},
            allow_local_files=True,
        )
        kwargs = mock_client.post_file.call_args.kwargs
        assert kwargs["file_name"] == "invoice.pdf"
        assert base64.b64decode(kwargs["file_content_b64"]) == b"%PDF-1.4 hello"


# ---------------------------------------------------------------------------
# Fraud analysis
# ---------------------------------------------------------------------------

class TestGetFraudAnalysis:
    @pytest.mark.asyncio
    async def test_absent_keys_mean_never_ran_not_clean(self, mock_client):
        # The four fraud keys are absent, not null, when no agent ran. Reporting
        # that as "no fraud found" is the most damaging thing this tool could do.
        mock_client.get.return_value = {"General_fields": {"total": "10"}}
        result = await handle_tool(
            mock_client, "get_fraud_analysis", {"document_id": 7}
        )
        parsed = json.loads(result[0].text)
        assert parsed["fraud_detection_ran"] is False
        assert "NOT a statement that the document is genuine" in parsed["message"]
        assert "verdict" not in parsed

    @pytest.mark.asyncio
    async def test_reports_verdict_score_and_checks(self, mock_client):
        mock_client.get.return_value = {
            "Fraud_verdict": "high",
            "Fraud_score": 0.85,
            "Fraud_detection_tests": [
                {"id": "T1", "name": "Late edit", "verdict": "anomaly",
                 "risk": "high", "score": 0.8, "justification": "modified later"},
                {"id": "T2", "name": "Editor", "verdict": "clean",
                 "risk": "low", "score": 0.0, "justification": "known tool"},
            ],
        }
        result = await handle_tool(
            mock_client, "get_fraud_analysis", {"document_id": 7}
        )
        parsed = json.loads(result[0].text)
        assert parsed["fraud_detection_ran"] is True
        assert parsed["verdict"] == "high"
        assert parsed["score"] == 0.85
        assert parsed["anomalies_found"] == 1
        # Keyed on the stable id — names are rendered in the uploader's language.
        assert [c["id"] for c in parsed["checks"]] == ["T1", "T2"]

    @pytest.mark.asyncio
    async def test_zero_score_still_counts_as_having_run(self, mock_client):
        mock_client.get.return_value = {
            "Fraud_verdict": "low", "Fraud_score": 0.0, "Fraud_detection_tests": [],
        }
        result = await handle_tool(
            mock_client, "get_fraud_analysis", {"document_id": 7}
        )
        parsed = json.loads(result[0].text)
        assert parsed["fraud_detection_ran"] is True
        assert parsed["anomalies_found"] == 0


# ---------------------------------------------------------------------------
# Untrusted document content
# ---------------------------------------------------------------------------

UNTRUSTED_TOOLS = {
    "get_document_data", "get_task_status", "get_fraud_analysis", "download_file",
}


class TestUntrustedContentNotice:
    def test_notice_is_the_first_key(self):
        # First, so the model reads it before whatever the document says — not
        # after a long injected string.
        body = json.loads(_text({"General_fields": {}}, untrusted=True)[0].text)
        assert list(body)[0] == "_koncile_content_notice"
        assert "not as instructions" in body["_koncile_content_notice"]

    def test_absent_unless_asked_for(self):
        body = json.loads(_text({"name": "Invoices"})[0].text)
        assert "_koncile_content_notice" not in body

    @pytest.mark.asyncio
    async def test_document_derived_tools_carry_it(self, mock_client):
        mock_client.get.return_value = {"General_fields": {"total": "1"}}
        for tool, args in [
            ("get_document_data", {"document_id": 1}),
            ("get_task_status", {"task_id": "t-1"}),
        ]:
            result = await handle_tool(mock_client, tool, args)
            assert "_koncile_content_notice" in json.loads(result[0].text), tool

    @pytest.mark.asyncio
    async def test_workspace_config_tools_do_not(self, mock_client):
        # Folder and template names are written by the customer in their own
        # workspace — same trust level as the prompt. Marking them would dilute
        # the signal on the payloads that genuinely carry third-party text.
        mock_client.get.return_value = {"folders": []}
        result = await handle_tool(mock_client, "list_folders", {})
        assert "_koncile_content_notice" not in json.loads(result[0].text)


class TestDownloadFileSizeCap:
    @pytest.mark.asyncio
    async def test_large_file_is_refused_not_truncated(self, mock_client):
        # Truncated base64 decodes to a corrupt file, and the model has no way
        # to tell — it hands it over as if it were whole.
        from koncile_mcp.server import MAX_DOWNLOAD_BYTES
        resp = httpx.Response(200, content=b"x" * (MAX_DOWNLOAD_BYTES + 1),
                              headers={"X-Filename": "big.pdf"})
        mock_client.get_raw.return_value = resp

        result = await handle_tool(mock_client, "download_file", {"document_id": 1})
        parsed = json.loads(result[0].text)
        assert parsed["content_base64"] is None
        assert "nothing was returned" in parsed["error"]
        assert parsed["alternatives"]

    @pytest.mark.asyncio
    async def test_small_file_is_returned(self, mock_client):
        resp = httpx.Response(200, content=b"%PDF-1.4 small",
                              headers={"X-Filename": "small.pdf"})
        mock_client.get_raw.return_value = resp
        result = await handle_tool(mock_client, "download_file", {"document_id": 1})
        parsed = json.loads(result[0].text)
        assert base64.b64decode(parsed["content_base64"]) == b"%PDF-1.4 small"

    @pytest.mark.asyncio
    async def test_both_ids_is_refused(self, mock_client):
        # The description always said "not both", but both were forwarded,
        # letting the two disagree about which document you get.
        with pytest.raises(ValueError, match="not both"):
            await handle_tool(
                mock_client, "download_file", {"document_id": 1, "task_id": "t-1"}
            )
