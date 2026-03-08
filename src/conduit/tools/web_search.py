"""A provider-agnostic web search tool for ADK agents."""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from conduit.config import Settings


def build_web_search_tool(settings: Settings):
    """Create a configured web-search tool closure."""

    async def web_search(query: str, max_results: int | None = None) -> str:
        """Search the public web and return a small set of result summaries.

        Args:
            query: The search query.
            max_results: Optional result limit. Defaults to the server config.

        Returns:
            A compact text summary of top search results.
        """

        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("query must not be empty")

        limit = max(1, min(max_results or settings.search_max_results, 10))
        search_errors: list[str] = []
        search_query = _normalize_navigational_query(cleaned_query)

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": settings.fetch_user_agent},
            timeout=settings.search_timeout_seconds,
        ) as client:
            results: list[dict[str, str]] = []
            provider = ""

            if settings.brave_api_key:
                results = await _search_brave_api(
                    client,
                    api_key=settings.brave_api_key,
                    query=search_query,
                    limit=limit,
                )
                provider = "brave-api"
                if not results:
                    search_errors.append("brave api returned no results")

            if not results:
                results = await _search_ecosia(client, search_query, limit)
                provider = "ecosia"
                if not results:
                    search_errors.append("ecosia returned no results")

        if results:
            return _format_search_results(cleaned_query, search_query, provider, results)
        if search_errors:
            return (
                f'No search results found for "{cleaned_query}". '
                f"Search backends reported: {'; '.join(search_errors)}."
            )
        return f'No search results found for "{cleaned_query}".'

    return web_search


async def _search_brave_api(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    limit: int,
) -> list[dict[str, str]]:
    """Query Brave Search API and extract top organic matches."""

    try:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={
                "q": query,
                "count": min(limit, 20),
            },
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    results: list[dict[str, str]] = []
    for item in response.json().get("web", {}).get("results", []):
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        snippet = (item.get("description") or "").strip()
        if not title or not url:
            continue

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
        if len(results) >= limit:
            break

    return results


async def _search_ecosia(
    client: httpx.AsyncClient,
    query: str,
    limit: int,
) -> list[dict[str, str]]:
    """Fallback search using Ecosia HTML results."""

    try:
        response = await client.get(
            "https://www.ecosia.org/search",
            params={
                "q": query,
            },
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []

    for result in soup.select("article.result.web-result"):
        title_link = result.select_one("a.result__link[href^='http']")
        snippet_node = result.select_one(".result__description")
        if title_link is None:
            continue

        title = title_link.get_text(" ", strip=True)
        url = (title_link.get("href") or "").strip()
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        if not title or not url:
            continue

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
        if len(results) >= limit:
            break

    return results


def _format_search_results(
    original_query: str,
    executed_query: str,
    provider: str,
    results: list[dict[str, str]],
) -> str:
    """Render tool results as compact plain text for the model."""

    if original_query == executed_query:
        lines = [f'Search results for "{original_query}" via {provider}:']
    else:
        lines = [
            f'Search results for "{original_query}" via {provider} '
            f'(searched for "{executed_query}"):'
        ]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result['title']}")
        lines.append(f"URL: {result['url']}")
        if result["snippet"]:
            lines.append(f"Snippet: {result['snippet']}")

    return "\n".join(lines)


def _normalize_navigational_query(query: str) -> str:
    """Bias homepage-like queries toward official-site matches."""

    lowered = query.lower()
    if "site:" in lowered or re.search(r"\b[\w-]+\.[a-z]{2,}\b", lowered):
        return query
    if not any(term in lowered for term in ("homepage", "home page", "website", "official site")):
        return query

    entity_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", query)
        if token.lower() not in {"home", "page", "homepage", "official", "website", "site"}
    ]
    if not entity_tokens:
        return query

    return f"{' '.join(entity_tokens)} official website"
