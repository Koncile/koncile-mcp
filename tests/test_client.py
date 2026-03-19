"""Tests for the Koncile HTTP client."""

import base64
import pytest
import httpx
import respx

BASE_URL = "http://test-api.local"


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_get_sends_auth_header(client):
    route = respx.get(f"{BASE_URL}/v1/fetch_all_folders").mock(
        return_value=httpx.Response(200, json={"folders": []})
    )
    result = await client.get("/v1/fetch_all_folders")
    assert result == {"folders": []}
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test-123"


@respx.mock
@pytest.mark.asyncio
async def test_get_filters_none_params(client):
    route = respx.get(f"{BASE_URL}/v1/fetch_documents").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    result = await client.get("/v1/fetch_documents", start_date="2024-01-01", end_date=None)
    assert result == [1, 2, 3]
    req_url = str(route.calls[0].request.url)
    assert "start_date=2024-01-01" in req_url
    assert "end_date" not in req_url


@respx.mock
@pytest.mark.asyncio
async def test_get_no_params(client):
    respx.get(f"{BASE_URL}/v1/fetch_all_folders").mock(
        return_value=httpx.Response(200, json={"folders": []})
    )
    result = await client.get("/v1/fetch_all_folders")
    assert result == {"folders": []}


@respx.mock
@pytest.mark.asyncio
async def test_get_does_not_add_trailing_slash(client):
    route = respx.get(f"{BASE_URL}/v1/fetch_folder").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    await client.get("/v1/fetch_folder", folder_id=1)
    assert not str(route.calls[0].request.url).rstrip("?").endswith("/v1/fetch_folder/")


# ---------------------------------------------------------------------------
# POST JSON
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_post_json_adds_trailing_slash(client):
    route = respx.post(f"{BASE_URL}/v1/create_folder/").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Test"})
    )
    result = await client.post_json("/v1/create_folder", {"name": "Test"})
    assert result["id"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_post_json_preserves_existing_trailing_slash(client):
    route = respx.post(f"{BASE_URL}/v1/create_folder/").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    await client.post_json("/v1/create_folder/", {"name": "Test"})
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_post_json_with_query_params(client):
    route = respx.post(f"{BASE_URL}/v1/create_template/").mock(
        return_value=httpx.Response(200, json={"id": 5})
    )
    result = await client.post_json("/v1/create_template", {"folder_id": 1, "name": "T"}, template_id=10)
    assert result["id"] == 5
    assert "template_id=10" in str(route.calls[0].request.url)


@respx.mock
@pytest.mark.asyncio
async def test_post_json_filters_none_query_params(client):
    route = respx.post(f"{BASE_URL}/v1/create_template/").mock(
        return_value=httpx.Response(200, json={"id": 5})
    )
    await client.post_json("/v1/create_template", {"folder_id": 1, "name": "T"}, template_id=None)
    assert "template_id" not in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# POST file (multipart)
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_post_file_decodes_base64(client):
    content = base64.b64encode(b"hello world").decode()
    route = respx.post(f"{BASE_URL}/v1/upload_file/").mock(
        return_value=httpx.Response(200, json={"task_ids": ["abc-123"]})
    )
    result = await client.post_file("/v1/upload_file", content, "test.txt")
    assert result["task_ids"] == ["abc-123"]
    assert b"hello world" in route.calls[0].request.content


@respx.mock
@pytest.mark.asyncio
async def test_post_file_sends_filename(client):
    content = base64.b64encode(b"data").decode()
    route = respx.post(f"{BASE_URL}/v1/upload_file/").mock(
        return_value=httpx.Response(200, json={})
    )
    await client.post_file("/v1/upload_file", content, "invoice.pdf")
    assert b"invoice.pdf" in route.calls[0].request.content


@respx.mock
@pytest.mark.asyncio
async def test_post_file_with_query_params(client):
    content = base64.b64encode(b"data").decode()
    route = respx.post(f"{BASE_URL}/v1/upload_file/").mock(
        return_value=httpx.Response(200, json={})
    )
    await client.post_file("/v1/upload_file", content, "f.pdf", folder_id=1, template_id=None)
    req_url = str(route.calls[0].request.url)
    assert "folder_id=1" in req_url
    assert "template_id" not in req_url


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_put_adds_trailing_slash(client):
    route = respx.put(f"{BASE_URL}/v1/update_folder/").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Updated"})
    )
    result = await client.put("/v1/update_folder", {"name": "Updated"}, folder_id=1)
    assert result["name"] == "Updated"
    assert "folder_id=1" in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_delete_adds_trailing_slash(client):
    route = respx.delete(f"{BASE_URL}/v1/delete_field/").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    await client.delete("/v1/delete_field", field_id=42)
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_delete_204_no_content(client):
    respx.delete(f"{BASE_URL}/v1/delete_field/").mock(
        return_value=httpx.Response(204)
    )
    result = await client.delete("/v1/delete_field", field_id=42)
    assert result == {"success": True}


@respx.mock
@pytest.mark.asyncio
async def test_delete_200_empty_body(client):
    respx.delete(f"{BASE_URL}/v1/delete_doc/").mock(
        return_value=httpx.Response(200, content=b"")
    )
    result = await client.delete("/v1/delete_doc", doc_id=1)
    assert result == {"success": True}


@respx.mock
@pytest.mark.asyncio
async def test_delete_200_with_json_body(client):
    respx.delete(f"{BASE_URL}/v1/delete_doc/").mock(
        return_value=httpx.Response(200, json={"deleted": True})
    )
    result = await client.delete("/v1/delete_doc", doc_id=1)
    assert result == {"deleted": True}


@respx.mock
@pytest.mark.asyncio
async def test_delete_200_non_json_body(client):
    respx.delete(f"{BASE_URL}/v1/delete_doc/").mock(
        return_value=httpx.Response(200, text="OK")
    )
    result = await client.delete("/v1/delete_doc", doc_id=1)
    assert result == {"success": True}


# ---------------------------------------------------------------------------
# get_raw
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_get_raw_returns_response(client):
    respx.get(f"{BASE_URL}/v1/fetch_file").mock(
        return_value=httpx.Response(
            200,
            content=b"PDF-binary-data",
            headers={"X-Filename": "invoice.pdf"},
        )
    )
    resp = await client.get_raw("/v1/fetch_file", document_id=1)
    assert resp.content == b"PDF-binary-data"
    assert resp.headers["X-Filename"] == "invoice.pdf"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_http_404_raises_runtime_error(client):
    respx.get(f"{BASE_URL}/v1/fetch_folder").mock(
        return_value=httpx.Response(404, json={"detail": "Folder not found"})
    )
    with pytest.raises(RuntimeError, match="Not found"):
        await client.get("/v1/fetch_folder", folder_id=999)


@respx.mock
@pytest.mark.asyncio
async def test_http_401_raises_auth_error(client):
    respx.get(f"{BASE_URL}/v1/fetch_all_folders").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with pytest.raises(RuntimeError, match="Authentication/permission error"):
        await client.get("/v1/fetch_all_folders")


@respx.mock
@pytest.mark.asyncio
async def test_http_422_raises_validation_error(client):
    respx.post(f"{BASE_URL}/v1/create_folder/").mock(
        return_value=httpx.Response(422, json={"detail": "name is required"})
    )
    with pytest.raises(RuntimeError, match="Validation error"):
        await client.post_json("/v1/create_folder", {})


@respx.mock
@pytest.mark.asyncio
async def test_http_500_raises_server_error(client):
    respx.get(f"{BASE_URL}/v1/fetch_all_folders").mock(
        return_value=httpx.Response(500, json={"detail": "Internal server error"})
    )
    with pytest.raises(RuntimeError, match="Server error"):
        await client.get("/v1/fetch_all_folders")


@respx.mock
@pytest.mark.asyncio
async def test_connection_error_raises_runtime_error(client):
    respx.get(f"{BASE_URL}/v1/fetch_all_folders").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    with pytest.raises(RuntimeError, match="Cannot reach Koncile API"):
        await client.get("/v1/fetch_all_folders")
