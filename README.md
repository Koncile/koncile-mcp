# Koncile MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that wraps the Koncile public API, allowing AI assistants to interact with Koncile natively — upload documents, check extraction status, manage folders/templates/fields, and more.

Works with any MCP-compatible client: Claude Code, Claude Desktop, Cursor, Windsurf, Cline, and others.

## Quickstart

No installation needed — connect directly to the hosted server with your API key:

```bash
# Claude Code
claude mcp add --transport http koncile https://mcp.koncile.ai/mcp \
  --header "Authorization: Bearer your-api-key"
```

For other MCP clients, use the URL `https://mcp.koncile.ai/mcp` with your API key as a Bearer token in the `Authorization` header.

## Local installation

```bash
# From source
git clone https://github.com/Koncile/koncile-mcp.git
cd koncile-mcp
pip install -e .

# Or directly
pip install koncile-mcp
```

## Configuration

The server needs a `KONCILE_API_KEY` to authenticate with the Koncile API. You can provide it in two ways:

### Option 1: Config file (recommended)

Create `~/.config/koncile/config`:

```bash
mkdir -p ~/.config/koncile
echo "KONCILE_API_KEY=your-api-key" > ~/.config/koncile/config
chmod 600 ~/.config/koncile/config
```

### Option 2: Environment variable

```bash
export KONCILE_API_KEY=your-api-key
```

Environment variables take precedence over the config file.

### Claude Code

```bash
claude mcp add --transport stdio koncile -- koncile-mcp
```

If using the environment variable approach instead of the config file:

```bash
claude mcp add --transport stdio -e KONCILE_API_KEY=your-api-key koncile -- koncile-mcp
```

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "koncile": {
      "command": "koncile-mcp"
    }
  }
}
```

If using the environment variable approach instead of the config file:

```json
{
  "mcpServers": {
    "koncile": {
      "command": "koncile-mcp",
      "env": {
        "KONCILE_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Available Tools (24 total)

### Core Document Workflow

| Tool | Description |
|------|-------------|
| `upload_file` | Upload a file (via local path or base64) for extraction. Returns task IDs. |
| `get_task_status` | Poll processing status of an upload task. |
| `list_documents` | List document IDs, optionally filtered by date range. |
| `get_document_data` | Get extracted fields and line items for a document. |
| `list_folders` | List all folders with their templates. |

### Folder Management

| Tool | Description |
|------|-------------|
| `get_folder` | Get folder details and templates. |
| `create_folder` | Create a new folder. |
| `update_folder` | Update folder name/description. |
| `delete_folder` | Delete a folder. |

### Template Management

| Tool | Description |
|------|-------------|
| `get_template` | Get template details, field IDs, instruction IDs. |
| `create_template` | Create a new template (optionally duplicate from existing). |
| `update_template` | Update template name/description. |
| `delete_template` | Delete a template. |

### Field Management

| Tool | Description |
|------|-------------|
| `get_field` | Get field details. |
| `create_field` | Create a field (type: "General fields" or "Line fields"). |
| `update_field` | Update field properties. |
| `delete_field` | Delete a field. |

### Instruction Management

| Tool | Description |
|------|-------------|
| `get_instruction` | Get instruction details. |
| `create_instruction` | Create an instruction for a template. |
| `update_instruction` | Update instruction content/type. |
| `delete_instruction` | Delete an instruction. |

### Utilities

| Tool | Description |
|------|-------------|
| `check_api_key` | Verify API key validity. |
| `download_file` | Download original file (returns base64). |
| `delete_document` | Delete a document. |

## Example Workflow

A typical document extraction workflow:

1. **Upload**: Use `upload_file` with a local file path (or base64-encoded content) → get `task_id`
2. **Poll**: Call `get_task_status` with `task_id` until status is `DONE`
3. **Read**: Use `get_document_data` to retrieve extracted fields and line items

```
User: Upload this invoice and extract the data
Claude: [calls upload_file with file_path="/path/to/invoice.pdf"] → task_id: "abc-123"
Claude: [calls get_task_status] → status: "PROCESSING"
Claude: [calls get_task_status] → status: "DONE", document_id: 42
Claude: [calls get_document_data] → General_fields: {vendor: "Acme", total: "1,234.56"}, Line_fields: [...]
```

## Self-hosting

You can run the MCP server as your own HTTP service:

```bash
# Run the HTTP server (default: 0.0.0.0:8080)
koncile-mcp-server

# Custom host/port
HOST=127.0.0.1 PORT=3000 koncile-mcp-server

# Or via Docker
docker build -t koncile-mcp .
docker run -p 8080:8080 -e KONCILE_API_URL=https://api.koncile.ai koncile-mcp
```

Clients authenticate with their Koncile API key as a Bearer token:

```bash
claude mcp add --transport http koncile https://your-host:8080/mcp \
  --header "Authorization: Bearer your-api-key"
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

### Testing with MCP Inspector

```bash
KONCILE_API_KEY=your-key \
  npx @modelcontextprotocol/inspector koncile-mcp

# Or against local dev API:
KONCILE_API_URL=http://localhost:8000 KONCILE_API_KEY=your-key \
  npx @modelcontextprotocol/inspector koncile-mcp
```

## License

MIT
