import json
from datetime import datetime, timezone

import httpx
import pytest

from digiposte_mcp.client import DigiposteAuthError, DigiposteClient, DigiposteError


def make_client(handler, **kwargs):
    kwargs.setdefault("access_token", "tok")
    return DigiposteClient(
        api_url="https://api.test/api",
        document_url="https://secure.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_requires_credentials():
    with pytest.raises(DigiposteAuthError):
        DigiposteClient()


async def test_list_folders_sends_bearer_and_json_headers():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"count": 0, "folders": []})

    async with make_client(handler) as client:
        assert await client.list_folders() == {"count": 0, "folders": []}
    req = seen["req"]
    assert str(req.url) == "https://api.test/api/v3/folders"
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["Accept"] == "application/json"


async def test_search_documents_body_and_params():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"documents": []})

    async with make_client(handler) as client:
        await client.search_documents(folder_id="f1", read=False, user_tags=["a"])
    req = seen["req"]
    assert req.method == "POST"
    assert req.url.path == "/api/v3/documents/search"
    assert req.url.params["max_results"] == "1000"
    assert req.url.params["sort"] == "TITLE"
    assert json.loads(req.content) == {
        "folder_id": "f1",
        "locations": ["INBOX", "SAFE"],
        "document_read": False,
        "user_tags": ["a"],
    }


async def test_search_documents_rejects_unknown_location():
    async with make_client(lambda r: httpx.Response(200, json={})) as client:
        with pytest.raises(ValueError):
            await client.search_documents(locations=["NOPE"])


async def test_trash_expects_204_and_check_param():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(204)

    async with make_client(handler) as client:
        await client.trash(["d1"], [])
    req = seen["req"]
    assert req.url.path == "/api/v3/file/tree/trash"
    assert req.url.params["check"] == "false"
    assert json.loads(req.content) == {"document_ids": ["d1"], "folder_ids": []}


async def test_move_uses_put_and_to_param():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(204)

    async with make_client(handler) as client:
        await client.move("dest id", [], ["f1"])
    assert seen["req"].method == "PUT"
    assert seen["req"].url.params["to"] == "dest id"


async def test_rename_escapes_path_segments():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"id": "x"})

    async with make_client(handler) as client:
        await client.rename_document("a/b", "mon fichier.pdf")
    assert seen["req"].url.raw_path == b"/api/v3/document/a%2Fb/rename/mon%20fichier.pdf"


async def test_api_error_list_is_reported():
    def handler(request):
        return httpx.Response(400, json=[{"error": "bad", "error_description": "oops"}])

    async with make_client(handler) as client:
        with pytest.raises(DigiposteError, match="bad: oops"):
            await client.list_folders()


async def test_redirect_to_authorize_is_auth_error():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://secure.test/v3/authorize?x=1"})

    async with make_client(handler) as client:
        with pytest.raises(DigiposteAuthError):
            await client.get_document_content("d1")


async def test_document_content_sends_bearer_and_cookie():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, content=b"%PDF", headers={"Content-Type": "application/pdf"})

    async with make_client(handler, cookie="a=b") as client:
        result = await client.get_document_content("d1")
    assert result.content == b"%PDF"
    assert result.content_type == "application/pdf"
    assert str(seen["req"].url) == "https://secure.test/rest/content/document/d1"
    assert seen["req"].headers["Cookie"] == "a=b"
    assert seen["req"].headers["Authorization"] == "Bearer tok"


async def test_token_obtained_from_cookie_then_refreshed_on_401():
    calls = []
    tokens = iter(["t1", "t2"])

    def handler(request):
        calls.append(request)
        if request.url.path == "/rest/security/token":
            assert request.headers["Cookie"] == "s=1"
            return httpx.Response(200, json={"access_token": next(tokens), "expires_at": 0})
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401, json=[{"error": "expired"}])
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler, access_token=None, cookie="s=1") as client:
        assert await client.get_profile() == {"ok": True}
    assert [c.url.path for c in calls] == [
        "/rest/security/token",
        "/api/v4/profile",
        "/rest/security/token",
        "/api/v4/profile",
    ]


async def test_create_document_multipart_fields():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"id": "new"})

    async with make_client(handler) as client:
        await client.create_document("a.pdf", b"data", folder_id="f1")
    req = seen["req"]
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    assert req.headers["X-Api-Version-Minor"] == "2"
    body = req.content
    fields = [b'name="health_document"', b'name="folder_id"', b'name="title"',
              b'name="archive"; filename="a.pdf"', b'name="archive_size"']
    positions = [body.index(f) for f in fields]
    assert positions == sorted(positions)  # même ordre que le SDK
    assert b"\r\n\r\ndata\r\n" in body


async def test_create_share_formats_dates_utc():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"id": "s"})

    async with make_client(handler) as client:
        await client.create_share("t", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert json.loads(seen["req"].content) == {"start_date": "2026-01-02T03:04:05Z", "title": "t"}
