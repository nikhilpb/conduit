"""Polymarket public market data tools for ADK agents."""

from __future__ import annotations

from typing import Any
from typing import Literal

import httpx

from conduit.config import Settings

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

LIST_MARKET_ORDER_MAP: dict[str, str] = {
    "volume_24hr": "volume24hr",
    "volume": "volume",
    "liquidity": "liquidityNum",
    "start_date": "startDate",
    "end_date": "endDate",
}


def build_polymarket_tools(settings: Settings) -> list:
    """Create the Polymarket tool closures exposed to the agent."""

    async def polymarket_search_markets(
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search Polymarket markets by keyword.

        Returns matching market IDs, slugs, outcome prices, and volume snapshots.
        """

        cleaned_query = _coerce_text(query)
        if not cleaned_query:
            raise ValueError("query must not be empty")

        bounded_limit = max(1, min(limit, 25))
        async with _build_http_client(settings) as client:
            payload = await _gamma_get(
                client,
                "/public-search",
                {"q": cleaned_query, "limit": bounded_limit},
            )

        results: list[dict[str, Any]] = []
        for event in payload.get("events", []):
            for market in event.get("markets", []):
                results.append(
                    _market_summary(market, default_event_slug=event.get("slug"))
                )
                if len(results) >= bounded_limit:
                    return results
        return results

    async def polymarket_list_markets(
        tag: str | None = None,
        active: bool = True,
        closed: bool = False,
        order: Literal[
            "volume_24hr",
            "volume",
            "liquidity",
            "start_date",
            "end_date",
        ] = "volume_24hr",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Browse Polymarket markets by tag, activity, and sort order."""

        bounded_limit = max(1, min(limit, 100))
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": LIST_MARKET_ORDER_MAP[order],
            "ascending": "false",
            "limit": bounded_limit,
            "offset": max(0, offset),
        }
        cleaned_tag = _coerce_text(tag)
        if cleaned_tag:
            params["tag_slug"] = cleaned_tag

        async with _build_http_client(settings) as client:
            markets = await _gamma_get(client, "/markets", params)

        return [_market_summary(market) for market in markets]

    async def polymarket_get_market(
        market_id: str | None = None,
        slug: str | None = None,
    ) -> dict[str, Any]:
        """Get one market's current prices, volume, liquidity, and live midpoint data."""

        cleaned_market_id = _coerce_text(market_id)
        cleaned_slug = _coerce_text(slug)
        if not cleaned_market_id and not cleaned_slug:
            raise ValueError("provide either market_id or slug")

        async with _build_http_client(settings) as client:
            market = await _resolve_market(
                client,
                market_id=cleaned_market_id,
                slug=cleaned_slug,
            )
            outcomes = _parse_json_field(market.get("outcomes"))
            token_ids = _parse_json_field(market.get("clobTokenIds"))
            live_pricing: list[dict[str, Any]] = []
            for index, outcome_name in enumerate(outcomes):
                if index >= len(token_ids):
                    break

                token_id = str(token_ids[index])
                midpoint = await _get_midpoint(client, token_id)
                spread_data = await _get_spread(client, token_id)
                live_entry: dict[str, Any] = {
                    "outcome": outcome_name,
                    "token_id": token_id,
                    "midpoint": midpoint,
                }
                spread = _coerce_float(spread_data.get("spread")) if spread_data else None
                if spread is not None:
                    live_entry["spread"] = spread
                live_pricing.append(live_entry)

        return {
            "id": str(market["id"]),
            "condition_id": market.get("conditionId", ""),
            "question": market.get("question", ""),
            "slug": market.get("slug", ""),
            "description": market.get("description", ""),
            "event_slug": _event_slug(market),
            "outcomes": _build_outcome_prices(market),
            "live_pricing": live_pricing,
            "volume": _volume_value(market),
            "volume_24hr": _coerce_float(market.get("volume24hr")) or 0.0,
            "liquidity": _coerce_float(market.get("liquidityNum") or market.get("liquidity"))
            or 0.0,
            "active": bool(market.get("active", False)),
            "closed": bool(market.get("closed", False)),
            "start_date": market.get("startDate"),
            "end_date": market.get("endDate"),
            "created_at": market.get("createdAt"),
        }

    async def polymarket_get_price_history(
        market_id: str,
        interval: Literal["max", "all", "1w", "1d", "6h", "1h"] = "1w",
    ) -> list[dict[str, Any]]:
        """Get historical Polymarket prices for each market outcome."""

        cleaned_market_id = _coerce_text(market_id)
        if not cleaned_market_id:
            raise ValueError("market_id must not be empty")

        async with _build_http_client(settings) as client:
            market = await _resolve_market(client, market_id=cleaned_market_id)
            outcomes = _parse_json_field(market.get("outcomes"))
            token_ids = _parse_json_field(market.get("clobTokenIds"))
            history_rows: list[dict[str, Any]] = []

            for index, outcome_name in enumerate(outcomes):
                if index >= len(token_ids):
                    break

                token_id = str(token_ids[index])
                history = await _get_price_history(client, token_id, interval=interval)
                history_rows.append(
                    {
                        "market_id": cleaned_market_id,
                        "outcome": outcome_name,
                        "history": [
                            {
                                "timestamp": point["t"],
                                "price": point["p"],
                            }
                            for point in history
                            if "t" in point and "p" in point
                        ],
                    }
                )

        return history_rows

    return [
        polymarket_search_markets,
        polymarket_list_markets,
        polymarket_get_market,
        polymarket_get_price_history,
    ]


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": settings.fetch_user_agent},
        timeout=settings.polymarket_timeout_seconds,
    )


async def _gamma_get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    response = await client.get(f"{GAMMA_BASE}{path}", params=params)
    response.raise_for_status()
    return response.json()


async def _clob_get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.get(f"{CLOB_BASE}{path}", params=params)
    response.raise_for_status()
    return response.json()


async def _resolve_market(
    client: httpx.AsyncClient,
    *,
    market_id: str | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    cleaned_market_id = _coerce_text(market_id)
    if cleaned_market_id:
        payload = await _gamma_get(client, f"/markets/{cleaned_market_id}")
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected Polymarket market payload for id {market_id!r}")
        return payload

    cleaned_slug = _coerce_text(slug)
    if not cleaned_slug:
        raise ValueError("provide either market_id or slug")

    payload = await _gamma_get(client, "/markets", {"slug": cleaned_slug})
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"no market found with slug: {slug}")
    if not isinstance(payload[0], dict):
        raise ValueError(f"unexpected Polymarket market payload for slug {slug!r}")
    return payload[0]


async def _get_midpoint(client: httpx.AsyncClient, token_id: str) -> float | None:
    try:
        payload = await _clob_get(client, "/midpoint", {"token_id": token_id})
    except httpx.HTTPStatusError:
        return None
    return _coerce_float(payload.get("mid"))


async def _get_spread(
    client: httpx.AsyncClient,
    token_id: str,
) -> dict[str, Any] | None:
    try:
        return await _clob_get(client, "/spread", {"token_id": token_id})
    except httpx.HTTPStatusError:
        return None


async def _get_price_history(
    client: httpx.AsyncClient,
    token_id: str,
    *,
    interval: str,
    fidelity: int = 60,
) -> list[dict[str, Any]]:
    try:
        payload = await _clob_get(
            client,
            "/prices-history",
            {
                "market": token_id,
                "interval": interval,
                "fidelity": fidelity,
            },
        )
    except httpx.HTTPStatusError:
        return []

    history = payload.get("history", [])
    return history if isinstance(history, list) else []


def _market_summary(
    market: dict[str, Any],
    *,
    default_event_slug: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(market["id"]),
        "question": market.get("question", ""),
        "slug": market.get("slug", ""),
        "outcomes": _build_outcome_prices(market),
        "volume": _volume_value(market),
        "volume_24hr": _coerce_float(market.get("volume24hr")) or 0.0,
        "active": bool(market.get("active", False)),
        "closed": bool(market.get("closed", False)),
        "event_slug": _event_slug(market) or default_event_slug,
    }


def _build_outcome_prices(market: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = _parse_json_field(market.get("outcomes"))
    prices = _parse_json_field(market.get("outcomePrices"))
    if not prices and len(outcomes) == 2:
        last_trade_price = _coerce_float(market.get("lastTradePrice"))
        if last_trade_price is not None and last_trade_price > 0:
            prices = [last_trade_price, round(1 - last_trade_price, 4)]
        else:
            best_bid = _coerce_float(market.get("bestBid"))
            best_ask = _coerce_float(market.get("bestAsk"))
            if best_bid is not None and best_ask is not None:
                midpoint = (best_bid + best_ask) / 2
                prices = [midpoint, round(1 - midpoint, 4)]

    outcome_prices: list[dict[str, Any]] = []
    for index, outcome_name in enumerate(outcomes):
        price = _coerce_float(prices[index]) if index < len(prices) else None
        outcome_prices.append(
            {
                "outcome": outcome_name,
                "price": price or 0.0,
            }
        )
    return outcome_prices


def _event_slug(market: dict[str, Any]) -> str | None:
    events = market.get("events")
    if isinstance(events, list) and events:
        first_event = events[0]
        if isinstance(first_event, dict):
            slug = first_event.get("slug")
            if isinstance(slug, str) and slug:
                return slug
    return None


def _volume_value(market: dict[str, Any]) -> float:
    return _coerce_float(market.get("volumeNum") or market.get("volume")) or 0.0


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json_field(value: str | list[Any] | None) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []

    stripped = value.strip()
    if not stripped:
        return []

    try:
        import json

        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return []

    return parsed if isinstance(parsed, list) else []
