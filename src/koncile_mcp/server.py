"""Koncile MCP Server — registers all tools and runs stdio transport."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from koncile_mcp.client import KoncileClient
from koncile_mcp.config import Config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(data: Any) -> list[TextContent]:
    """Wrap a JSON-serialisable value as MCP text content."""
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _require(args: dict, *keys: str) -> None:
    """Raise if any required key is missing from args."""
    missing = [k for k in keys if k not in args or args[k] is None]
    if missing:
        raise ValueError(f"Missing required parameters: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # ── P0: Core document workflow ─────────────────────────────────────────
    Tool(
        name="upload_file",
        description=(
            "Upload a file for document extraction. Provide either file_path (path to a local file) "
            "or file_content (base64-encoded content). If file_path is used, file_name is derived "
            "from the path unless explicitly provided. "
            "Returns task IDs that can be polled with get_task_status. "
            "Optionally specify folder_id and/or template_id to route the file."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to a local file to upload (alternative to file_content)"},
                "file_content": {"type": "string", "description": "Base64-encoded file content (alternative to file_path)"},
                "file_name": {"type": "string", "description": "Original filename including extension (e.g. 'invoice.pdf'). Required if using file_content, optional with file_path."},
                "folder_id": {"type": "integer", "description": "Target folder ID (optional)"},
                "template_id": {"type": "integer", "description": "Target template ID (optional)"},
                "metadata": {"type": "string", "description": "Optional metadata string"},
            },
        },
    ),
    Tool(
        name="get_task_status",
        description=(
            "Poll the processing status of an upload task. Returns status (PENDING, PROCESSING, "
            "DONE, FAILED, DUPLICATE) and, when DONE, the extracted document data including "
            "General_fields and Line_fields."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID returned by upload_file"},
            },
            "required": ["task_id"],
        },
    ),
    Tool(
        name="list_documents",
        description="List document IDs, optionally filtered by date range (YYYY-MM-DD).",
        inputSchema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date filter (YYYY-MM-DD, inclusive)"},
                "end_date": {"type": "string", "description": "End date filter (YYYY-MM-DD, inclusive)"},
            },
        },
    ),
    Tool(
        name="get_document_data",
        description="Get extracted fields and line items for a document.",
        inputSchema={
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "description": "Document ID"},
            },
            "required": ["document_id"],
        },
    ),
    Tool(
        name="list_folders",
        description="List all folders with their templates. No parameters required.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── P1: Configuration management ──────────────────────────────────────
    Tool(
        name="get_folder",
        description="Get details of a folder including its templates.",
        inputSchema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "integer", "description": "Folder ID"},
            },
            "required": ["folder_id"],
        },
    ),
    Tool(
        name="create_folder",
        description="Create a new folder.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Folder name"},
                "desc": {"type": "string", "description": "Folder description (optional)"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="update_folder",
        description="Update an existing folder's name or description.",
        inputSchema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "integer", "description": "Folder ID to update"},
                "name": {"type": "string", "description": "New name (optional)"},
                "desc": {"type": "string", "description": "New description (optional)"},
            },
            "required": ["folder_id"],
        },
    ),
    Tool(
        name="delete_folder",
        description="Delete a folder. Set override=true to delete even if it contains templates.",
        inputSchema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "integer", "description": "Folder ID to delete"},
                "override": {"type": "boolean", "description": "Force delete even if folder has templates (default: false)"},
            },
            "required": ["folder_id"],
        },
    ),
    Tool(
        name="get_template",
        description="Get details of a template including its field and instruction IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID"},
            },
            "required": ["template_id"],
        },
    ),
    Tool(
        name="create_template",
        description=(
            "Create a new template in a folder. Optionally duplicate from an existing template "
            "by providing duplicate_from (template_id to copy)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "folder_id": {"type": "integer", "description": "Folder ID to create the template in"},
                "name": {"type": "string", "description": "Template name"},
                "desc": {"type": "string", "description": "Template description (optional)"},
                "duplicate_from": {"type": "integer", "description": "Template ID to duplicate from (optional)"},
            },
            "required": ["folder_id", "name"],
        },
    ),
    Tool(
        name="update_template",
        description="Update an existing template's name or description.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to update"},
                "name": {"type": "string", "description": "New name (optional)"},
                "desc": {"type": "string", "description": "New description (optional)"},
            },
            "required": ["template_id"],
        },
    ),
    Tool(
        name="delete_template",
        description="Delete a template. Set override=true to delete even if it has documents.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to delete"},
                "override": {"type": "boolean", "description": "Force delete (default: false)"},
            },
            "required": ["template_id"],
        },
    ),
    Tool(
        name="get_field",
        description="Get details of a field (parameter).",
        inputSchema={
            "type": "object",
            "properties": {
                "field_id": {"type": "integer", "description": "Field ID"},
            },
            "required": ["field_id"],
        },
    ),
    Tool(
        name="create_field",
        description=(
            "Create a new field in a template. Type must be 'General fields' or 'Line fields'. "
            "Format examples: 'string', 'number', 'date', 'amount'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to add the field to"},
                "name": {"type": "string", "description": "Field name"},
                "type": {"type": "string", "description": "Field type: 'General fields' or 'Line fields'"},
                "format": {"type": "string", "description": "Field format (e.g. 'string', 'number', 'date', 'amount')"},
                "desc": {"type": "string", "description": "Field description (optional)"},
                "position": {"type": "integer", "description": "Position/order (optional)"},
            },
            "required": ["template_id", "name", "type", "format"],
        },
    ),
    Tool(
        name="update_field",
        description="Update an existing field's properties.",
        inputSchema={
            "type": "object",
            "properties": {
                "field_id": {"type": "integer", "description": "Field ID to update"},
                "name": {"type": "string", "description": "New name (optional)"},
                "type": {"type": "string", "description": "New type (optional)"},
                "format": {"type": "string", "description": "New format (optional)"},
                "desc": {"type": "string", "description": "New description (optional)"},
            },
            "required": ["field_id"],
        },
    ),
    Tool(
        name="delete_field",
        description="Delete a field.",
        inputSchema={
            "type": "object",
            "properties": {
                "field_id": {"type": "integer", "description": "Field ID to delete"},
            },
            "required": ["field_id"],
        },
    ),
    Tool(
        name="get_instruction",
        description="Get details of an instruction.",
        inputSchema={
            "type": "object",
            "properties": {
                "instruction_id": {"type": "integer", "description": "Instruction ID"},
            },
            "required": ["instruction_id"],
        },
    ),
    Tool(
        name="create_instruction",
        description=(
            "Create a new instruction for a template. Type must be 'General fields' or 'Line fields'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "integer", "description": "Template ID to add the instruction to"},
                "content": {"type": "string", "description": "Instruction content text"},
                "type": {"type": "string", "description": "Instruction type: 'General fields' or 'Line fields'"},
            },
            "required": ["template_id", "content", "type"],
        },
    ),
    Tool(
        name="update_instruction",
        description="Update an existing instruction's content or type.",
        inputSchema={
            "type": "object",
            "properties": {
                "instruction_id": {"type": "integer", "description": "Instruction ID to update"},
                "content": {"type": "string", "description": "New content (optional)"},
                "type": {"type": "string", "description": "New type (optional)"},
            },
            "required": ["instruction_id"],
        },
    ),
    Tool(
        name="delete_instruction",
        description="Delete an instruction.",
        inputSchema={
            "type": "object",
            "properties": {
                "instruction_id": {"type": "integer", "description": "Instruction ID to delete"},
            },
            "required": ["instruction_id"],
        },
    ),
    # ── P2: Utilities ─────────────────────────────────────────────────────
    Tool(
        name="check_api_key",
        description="Verify that the configured API key is valid.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="download_file",
        description=(
            "Download the original file for a document. Returns base64-encoded content. "
            "Provide either document_id or task_id (not both)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "description": "Document ID"},
                "task_id": {"type": "string", "description": "Task ID"},
            },
        },
    ),
    Tool(
        name="delete_document",
        description="Delete a document. Optionally delete the underlying file too.",
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer", "description": "Document ID to delete"},
                "delete_file": {"type": "boolean", "description": "Also delete the file (default: false)"},
            },
            "required": ["doc_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def handle_tool(client: KoncileClient, name: str, args: dict[str, Any]) -> list[TextContent]:
    """Dispatch a tool call to the appropriate client method."""

    # ── P0 ────────────────────────────────────────────────────────────────
    if name == "upload_file":
        file_path = args.get("file_path")
        file_content = args.get("file_content")
        file_name = args.get("file_name")

        if file_path:
            p = Path(file_path).expanduser().resolve()
            if not p.is_file():
                raise ValueError(f"File not found: {p}")
            file_content = base64.b64encode(p.read_bytes()).decode()
            if not file_name:
                file_name = p.name
        elif file_content:
            if not file_name:
                raise ValueError("file_name is required when using file_content")
        else:
            raise ValueError("Either file_path or file_content is required")

        result = await client.post_file(
            "/v1/upload_file",
            file_content_b64=file_content,
            file_name=file_name,
            folder_id=args.get("folder_id"),
            template_id=args.get("template_id"),
            metadata=args.get("metadata"),
        )
        return _text(result)

    if name == "get_task_status":
        _require(args, "task_id")
        result = await client.get("/v1/fetch_tasks_results", task_id=args["task_id"])
        return _text(result)

    if name == "list_documents":
        result = await client.get(
            "/v1/fetch_documents",
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
        )
        return _text(result)

    if name == "get_document_data":
        _require(args, "document_id")
        result = await client.get("/v1/fetch_document_data", document_id=args["document_id"])
        return _text(result)

    if name == "list_folders":
        result = await client.get("/v1/fetch_all_folders")
        return _text(result)

    # ── P1: Folders ───────────────────────────────────────────────────────
    if name == "get_folder":
        _require(args, "folder_id")
        result = await client.get("/v1/fetch_folder", folder_id=args["folder_id"])
        return _text(result)

    if name == "create_folder":
        _require(args, "name")
        body = {"name": args["name"]}
        if args.get("desc"):
            body["desc"] = args["desc"]
        result = await client.post_json("/v1/create_folder", body)
        return _text(result)

    if name == "update_folder":
        _require(args, "folder_id")
        body: dict[str, Any] = {}
        if args.get("name") is not None:
            body["name"] = args["name"]
        if args.get("desc") is not None:
            body["desc"] = args["desc"]
        result = await client.put("/v1/update_folder", body, folder_id=args["folder_id"])
        return _text(result)

    if name == "delete_folder":
        _require(args, "folder_id")
        result = await client.delete(
            "/v1/delete_folder",
            folder_id=args["folder_id"],
            override=args.get("override", False),
        )
        return _text(result)

    # ── P1: Templates ────────────────────────────────────────────────────
    if name == "get_template":
        _require(args, "template_id")
        result = await client.get("/v1/fetch_template", template_id=args["template_id"])
        return _text(result)

    if name == "create_template":
        _require(args, "folder_id", "name")
        body: dict[str, Any] = {"folder_id": args["folder_id"], "name": args["name"]}
        if args.get("desc"):
            body["desc"] = args["desc"]
        result = await client.post_json(
            "/v1/create_template",
            body,
            template_id=args.get("duplicate_from"),
        )
        return _text(result)

    if name == "update_template":
        _require(args, "template_id")
        body: dict[str, Any] = {}
        if args.get("name") is not None:
            body["name"] = args["name"]
        if args.get("desc") is not None:
            body["desc"] = args["desc"]
        result = await client.put("/v1/update_template", body, template_id=args["template_id"])
        return _text(result)

    if name == "delete_template":
        _require(args, "template_id")
        result = await client.delete(
            "/v1/delete_template",
            template_id=args["template_id"],
            override=args.get("override", False),
        )
        return _text(result)

    # ── P1: Fields ────────────────────────────────────────────────────────
    if name == "get_field":
        _require(args, "field_id")
        result = await client.get("/v1/fetch_field", field_id=args["field_id"])
        return _text(result)

    if name == "create_field":
        _require(args, "template_id", "name", "type", "format")
        body: dict[str, Any] = {
            "template_id": args["template_id"],
            "name": args["name"],
            "type": args["type"],
            "format": args["format"],
        }
        if args.get("desc"):
            body["desc"] = args["desc"]
        if args.get("position") is not None:
            body["position"] = args["position"]
        result = await client.post_json("/v1/create_field", body)
        return _text(result)

    if name == "update_field":
        _require(args, "field_id")
        body: dict[str, Any] = {}
        for key in ("name", "type", "format", "desc"):
            if args.get(key) is not None:
                body[key] = args[key]
        result = await client.put("/v1/update_field", body, field_id=args["field_id"])
        return _text(result)

    if name == "delete_field":
        _require(args, "field_id")
        result = await client.delete("/v1/delete_field", field_id=args["field_id"])
        return _text(result)

    # ── P1: Instructions ─────────────────────────────────────────────────
    if name == "get_instruction":
        _require(args, "instruction_id")
        result = await client.get("/v1/fetch_instruction", instruction_id=args["instruction_id"])
        return _text(result)

    if name == "create_instruction":
        _require(args, "template_id", "content", "type")
        body = {
            "template_id": args["template_id"],
            "content": args["content"],
            "type": args["type"],
        }
        result = await client.post_json("/v1/create_instruction", body)
        return _text(result)

    if name == "update_instruction":
        _require(args, "instruction_id")
        body: dict[str, Any] = {}
        if args.get("content") is not None:
            body["content"] = args["content"]
        if args.get("type") is not None:
            body["type"] = args["type"]
        result = await client.put(
            "/v1/update_instruction", body, instruction_id=args["instruction_id"],
        )
        return _text(result)

    if name == "delete_instruction":
        _require(args, "instruction_id")
        result = await client.delete(
            "/v1/delete_instruction", instruction_id=args["instruction_id"],
        )
        return _text(result)

    # ── P2: Utilities ─────────────────────────────────────────────────────
    if name == "check_api_key":
        result = await client.post_json("/v1/check_api_key", {})
        return _text(result)

    if name == "download_file":
        if not args.get("document_id") and not args.get("task_id"):
            raise ValueError("Either document_id or task_id is required")
        resp = await client.get_raw(
            "/v1/fetch_file",
            document_id=args.get("document_id"),
            task_id=args.get("task_id"),
        )
        encoded = base64.b64encode(resp.content).decode()
        filename = resp.headers.get("X-Filename", "file")
        return _text({"filename": filename, "content_base64": encoded})

    if name == "delete_document":
        _require(args, "doc_id")
        result = await client.delete(
            "/v1/delete_doc",
            doc_id=args["doc_id"],
            delete_file=args.get("delete_file", False),
        )
        return _text(result)

    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server(config: Config) -> Server:
    """Create and configure the MCP server."""
    server = Server("koncile")
    client = KoncileClient(config)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return await handle_tool(client, name, arguments or {})

    return server


def main() -> None:
    """Entry point — load config and run the MCP server over stdio."""
    config = Config.from_env()
    server = create_server(config)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
