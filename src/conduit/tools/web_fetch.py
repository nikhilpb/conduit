"""A lightweight webpage fetch tool for ADK agents."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from conduit.config import Settings


def build_web_fetch_tool(settings: Settings):
    """Create a configured web-fetch tool closure."""

    async def web_fetch(url: str) -> dict[str, Any]:
        """Fetch a public webpage and return cleaned text content.

        Args:
            url: The HTTP or HTTPS URL to retrieve.

        Returns:
            Metadata and cleaned text content for the fetched page.
        """

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be a valid http or https URL")

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": settings.fetch_user_agent},
            timeout=settings.fetch_timeout_seconds,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        resolved_url = str(response.url)

        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            title, cleaned_text = _extract_html_text(
                response.text,
                max_chars=settings.fetch_max_chars,
            )
            return {
                "url": resolved_url,
                "status_code": response.status_code,
                "content_type": content_type,
                "title": title,
                "content": cleaned_text,
                "truncated": len(cleaned_text) >= settings.fetch_max_chars,
            }

        if content_type.startswith("text/") or "json" in content_type:
            content = response.text[: settings.fetch_max_chars]
            return {
                "url": resolved_url,
                "status_code": response.status_code,
                "content_type": content_type,
                "title": None,
                "content": content,
                "truncated": len(response.text) > len(content),
            }

        return {
            "url": resolved_url,
            "status_code": response.status_code,
            "content_type": content_type,
            "title": None,
            "content": "",
            "truncated": False,
            "note": "Binary content is not returned by web_fetch.",
        }

    return web_fetch


def _extract_html_text(html: str, *, max_chars: int) -> tuple[str | None, str]:
    """Extract cleaned text content from HTML."""

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else None

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    lines = [line.strip() for line in soup.get_text("\n").splitlines()]
    cleaned_text = "\n".join(line for line in lines if line)
    return title, cleaned_text[:max_chars]

