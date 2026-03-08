import pytest
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from conduit.config import Settings
from conduit.tools import web_search as web_search_module


def _mock_response(*, json_data=None, text="", status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.text = text
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.anyio
async def test_search_brave_api_parses_response():
    client = AsyncMock()
    client.get.return_value = _mock_response(
        json_data={
            "web": {
                "results": [
                    {
                        "title": "OpenAI",
                        "url": "https://openai.com/",
                        "description": "Official site",
                    },
                    {
                        "title": "Platform",
                        "url": "https://platform.openai.com/",
                        "description": "Developer platform",
                    },
                ]
            }
        }
    )

    results = await web_search_module._search_brave_api(
        client,
        api_key="test-key",
        query="OpenAI official website",
        limit=5,
    )

    assert results == [
        {
            "title": "OpenAI",
            "url": "https://openai.com/",
            "snippet": "Official site",
        },
        {
            "title": "Platform",
            "url": "https://platform.openai.com/",
            "snippet": "Developer platform",
        },
    ]
    call = client.get.call_args
    assert call.args[0] == "https://api.search.brave.com/res/v1/web/search"
    assert call.kwargs["headers"]["X-Subscription-Token"] == "test-key"
    assert call.kwargs["params"]["count"] == 5


@pytest.mark.anyio
async def test_web_search_tool_formats_brave_results(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")

    async def fake_search_brave_api(client, api_key, query, limit):
        assert api_key == "test-key"
        assert query == "OpenAI official website"
        assert limit == 3
        return [
            {
                "title": "OpenAI",
                "url": "https://openai.com/",
                "snippet": "Official site",
            }
        ]

    async def fake_search_ecosia(client, query, limit):
        raise AssertionError("ecosia fallback should not be used when brave returns results")

    monkeypatch.setattr(web_search_module, "_search_brave_api", fake_search_brave_api)
    monkeypatch.setattr(web_search_module, "_search_ecosia", fake_search_ecosia)

    tool = web_search_module.build_web_search_tool(
        Settings(_env_file=None, search_max_results=3)
    )

    result = await tool("OpenAI homepage")

    assert 'Search results for "OpenAI homepage" via brave-api' in result
    assert "URL: https://openai.com/" in result
