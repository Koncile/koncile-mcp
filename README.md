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

# Or from PyPI
pip install koncile-mcp
```

The HTTP transport (`koncile-mcp-server`) needs an extra:

```bash
pip install "koncile-mcp[http]"
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

## Sending a document

The hosted server runs in the cloud, so it cannot read a file off your machine
and a PDF is far too large to pass through the conversation as text. Uploading
is therefore two steps:

```
create_upload_links(["invoice.pdf"])   ->  an upload URL per file
   upload the file to that URL          (the returned curl command does it)
register_uploaded_files([upload_id])   ->  task IDs
get_task_status(task_id)               ->  poll until DONE
get_document_data(document_id)         ->  the extracted fields
```

In Claude Code or Cursor the assistant can run the upload command itself, so
this is a single request with no manual step. Elsewhere, paste the command.

`upload_file` is the one-step alternative and still works, but only for small
files: it carries the file as base64 through the conversation. Its `file_path`
option is available **only when running locally over stdio** — the hosted server
has no access to your filesystem and says so rather than failing obscurely.

## Available Tools (27 total)

Every tool is annotated with a title and a read/write hint, so clients can tell
which ones only read and which ones change or delete your data. The nine
destructive tools (`update_*` and `delete_*`) are marked as such and most clients
will ask you to confirm before running one.

### Core Document Workflow

| Tool | Description |
|------|-------------|
| `create_upload_links` | Get upload URLs for one or more files (step 1 of 2). |
| `register_uploaded_files` | Start extraction for uploaded files (step 2 of 2). Returns task IDs. |
| `upload_file` | One-step upload for small files (base64; local path when running over stdio). |
| `get_task_status` | Poll processing status of an upload task. |
| `list_documents` | List document IDs, optionally filtered by date range. |
| `get_document_data` | Get extracted fields and line items for a document. |
| `get_fraud_analysis` | Get the fraud verdict, score and per-check findings for a document. |
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

## Limits and privacy

- **Tool results are capped** at roughly 150,000 characters by most clients.
  `download_file` refuses a file too large to fit rather than returning a
  truncated base64 string, which would decode to a corrupt file.
- **The hosted server stores nothing.** Your API key is read from the
  `Authorization` header on each request and discarded; it is never written to
  a log or to disk.
- **What is logged**: the tool name, how long it took, whether it succeeded, and
  a salted one-way fingerprint of your API key so usage can be attributed
  without the key ever being recorded, and the ids of the objects acted on
  (documents, tasks, folders, templates, fields, instructions). File names, file contents, extracted
  values and instruction text are never logged.
- **Document content is untrusted.** Tool results carrying text extracted from a
  document include a `_koncile_content_notice` field reminding the assistant
  that the text is data to report, not instructions to follow — the document was
  written by a third party.
