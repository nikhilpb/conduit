import pytest

from conduit.config import Settings
from conduit.tools import polymarket as polymarket_module


def _tool_map() -> dict[str, object]:
    return {
        tool.__name__: tool
        for tool in polymarket_module.build_polymarket_tools(Settings(_env_file=None))
    }


@pytest.mark.anyio
async def test_polymarket_search_markets_returns_market_summaries(monkeypatch):
    async def fake_gamma_get(client, path, params=None):
        assert path == "/public-search"
        assert params == {"q": "fed rates", "limit": 2}
        return {
            "events": [
                {
                    "slug": "fed-march-decision",
                    "markets": [
                        {
                            "id": 101,
                            "question": "Will the Fed cut rates in March?",
                            "slug": "fed-cut-rates-march",
                            "outcomes": '["Yes", "No"]',
                            "outcomePrices": '["0.41", "0.59"]',
                            "volumeNum": "1250000.50",
                            "volume24hr": "240000.25",
                            "active": True,
                            "closed": False,
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(polymarket_module, "_gamma_get", fake_gamma_get)

    results = await _tool_map()["polymarket_search_markets"]("fed rates", limit=2)

    assert results == [
        {
            "id": "101",
            "question": "Will the Fed cut rates in March?",
            "slug": "fed-cut-rates-march",
            "outcomes": [
                {"outcome": "Yes", "price": 0.41},
                {"outcome": "No", "price": 0.59},
            ],
            "volume": 1250000.5,
            "volume_24hr": 240000.25,
            "active": True,
            "closed": False,
            "event_slug": "fed-march-decision",
        }
    ]


@pytest.mark.anyio
async def test_polymarket_list_markets_maps_sort_order(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_gamma_get(client, path, params=None):
        captured["path"] = path
        captured["params"] = params
        return []

    monkeypatch.setattr(polymarket_module, "_gamma_get", fake_gamma_get)

    results = await _tool_map()["polymarket_list_markets"](
        tag="crypto",
        order="liquidity",
        limit=200,
        offset=-4,
    )

    assert results == []
    assert captured == {
        "path": "/markets",
        "params": {
            "active": "true",
            "closed": "false",
            "order": "liquidityNum",
            "ascending": "false",
            "limit": 100,
            "offset": 0,
            "tag_slug": "crypto",
        },
    }


@pytest.mark.anyio
async def test_polymarket_get_market_includes_live_pricing(monkeypatch):
    async def fake_gamma_get(client, path, params=None):
        assert path == "/markets/123"
        return {
            "id": 123,
            "conditionId": "condition-123",
            "question": "Will BTC close above $100k this month?",
            "slug": "btc-above-100k-month-close",
            "description": "Monthly BTC close market.",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.62", "0.38"]',
            "clobTokenIds": '["token-yes", "token-no"]',
            "events": [{"slug": "bitcoin-monthly"}],
            "volumeNum": "5000000",
            "volume24hr": "800000",
            "liquidityNum": "1200000",
            "active": True,
            "closed": False,
            "startDate": "2026-03-01T00:00:00Z",
            "endDate": "2026-03-31T23:59:59Z",
            "createdAt": "2026-02-20T10:00:00Z",
        }

    async def fake_get_midpoint(client, token_id):
        return {
            "token-yes": 0.621,
            "token-no": 0.379,
        }[token_id]

    async def fake_get_spread(client, token_id):
        return {
            "token-yes": {"spread": "0.012"},
            "token-no": {"spread": "0.011"},
        }[token_id]

    monkeypatch.setattr(polymarket_module, "_gamma_get", fake_gamma_get)
    monkeypatch.setattr(polymarket_module, "_get_midpoint", fake_get_midpoint)
    monkeypatch.setattr(polymarket_module, "_get_spread", fake_get_spread)

    result = await _tool_map()["polymarket_get_market"](market_id="123")

    assert result == {
        "id": "123",
        "condition_id": "condition-123",
        "question": "Will BTC close above $100k this month?",
        "slug": "btc-above-100k-month-close",
        "description": "Monthly BTC close market.",
        "event_slug": "bitcoin-monthly",
        "outcomes": [
            {"outcome": "Yes", "price": 0.62},
            {"outcome": "No", "price": 0.38},
        ],
        "live_pricing": [
            {
                "outcome": "Yes",
                "token_id": "token-yes",
                "midpoint": 0.621,
                "spread": 0.012,
            },
            {
                "outcome": "No",
                "token_id": "token-no",
                "midpoint": 0.379,
                "spread": 0.011,
            },
        ],
        "volume": 5000000.0,
        "volume_24hr": 800000.0,
        "liquidity": 1200000.0,
        "active": True,
        "closed": False,
        "start_date": "2026-03-01T00:00:00Z",
        "end_date": "2026-03-31T23:59:59Z",
        "created_at": "2026-02-20T10:00:00Z",
    }


@pytest.mark.anyio
async def test_polymarket_get_price_history_returns_series(monkeypatch):
    async def fake_gamma_get(client, path, params=None):
        assert path == "/markets/123"
        return {
            "id": 123,
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["token-yes", "token-no"]',
        }

    async def fake_get_price_history(client, token_id, *, interval, fidelity=60):
        assert interval == "1d"
        assert fidelity == 60
        return [
            {"t": 1710000000, "p": 0.45 if token_id == "token-yes" else 0.55},
            {"t": 1710003600, "p": 0.47 if token_id == "token-yes" else 0.53},
        ]

    monkeypatch.setattr(polymarket_module, "_gamma_get", fake_gamma_get)
    monkeypatch.setattr(polymarket_module, "_get_price_history", fake_get_price_history)

    result = await _tool_map()["polymarket_get_price_history"](
        market_id="123",
        interval="1d",
    )

    assert result == [
        {
            "market_id": "123",
            "outcome": "Yes",
            "history": [
                {"timestamp": 1710000000, "price": 0.45},
                {"timestamp": 1710003600, "price": 0.47},
            ],
        },
        {
            "market_id": "123",
            "outcome": "No",
            "history": [
                {"timestamp": 1710000000, "price": 0.55},
                {"timestamp": 1710003600, "price": 0.53},
            ],
        },
    ]
