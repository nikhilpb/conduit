import httpx
import pytest

from conduit.config import Settings
from conduit.tools import web_fetch as web_fetch_module


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str) -> httpx.Response:
        del url
        return self._response


@pytest.mark.anyio
async def test_web_fetch_returns_error_payload_for_invalid_url():
    tool = web_fetch_module.build_web_fetch_tool(Settings(_env_file=None))

    result = await tool("not-a-url")

    assert result == {
        "ok": False,
        "url": "not-a-url",
        "status_code": None,
        "content_type": None,
        "title": None,
        "content": "",
        "truncated": False,
        "error": "url must be a valid http or https URL",
    }


@pytest.mark.anyio
async def test_web_fetch_returns_error_payload_for_http_status(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.com/blocked",
    )
    response = httpx.Response(
        403,
        request=request,
        headers={"content-type": "text/html"},
    )
    monkeypatch.setattr(
        web_fetch_module.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response),
    )
    tool = web_fetch_module.build_web_fetch_tool(Settings(_env_file=None))

    result = await tool("https://example.com/blocked")

    assert result == {
        "ok": False,
        "url": "https://example.com/blocked",
        "status_code": 403,
        "content_type": None,
        "title": None,
        "content": "",
        "truncated": False,
        "error": "HTTP 403 Forbidden",
    }
