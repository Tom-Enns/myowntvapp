"""Sportsurge site handler — handles sportsurge.ws stream pages.

Sportsurge uses cx-iframe but sets the src dynamically via a
changeStream() JS function instead of a static src attribute.
We need to parse the JS to extract the base embed URL and the
first available streamId from the stream buttons.
"""

import logging
import re

import aiohttp
from bs4 import BeautifulSoup

from app.resolvers.base import ResolvedEmbed
from app.resolvers.registry import get_resolver_registry
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class SportsurgeSite(StreamSite):
    """Handles stream extraction from Sportsurge (sportsurge.ws) pages."""

    @property
    def site_id(self) -> str:
        return "sportsurge"

    @property
    def domains(self) -> list[str]:
        return [
            "sportsurge.ws",
            "sportsurge.fit",
            "sportsurge.net",
            "sportsurge.club",
            "sportsurge100.is",
        ]

    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[sportsurge] Page returned {resp.status}: {url[:80]}")
                    return None
                page_html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[sportsurge] Failed to fetch {url[:80]}: {e}")
            return None

        # Extract the embed URL from changeStream() JS function
        # Pattern: getElementById('cx-iframe').src = 'https://.../' + streamId
        iframe_url = self._extract_iframe_url(page_html)
        if not iframe_url:
            logger.debug(f"[sportsurge] No iframe URL found on {url[:80]}")
            return None

        # Resolve the embed (same gooz.aapmains.net as StreamEast)
        resolver_registry = get_resolver_registry()
        return await resolver_registry.resolve(iframe_url, page_url, session)

    def _extract_iframe_url(self, html: str) -> str | None:
        """Extract the full iframe URL from sportsurge's changeStream() JS.

        The page has:
          1. A changeStream() function with the base embed URL
          2. Stream buttons with onclick="changeStream('streamId')"
        We combine these to get the full iframe URL.
        """
        # Extract the base embed URL from the changeStream function
        # Pattern: .src = 'https://gooz.aapmains.net/new-stream-embed/' + streamId
        base_match = re.search(
            r"""getElementById\(['"](cx-iframe|wp_player)['"]\)\.src\s*=\s*['"]([^'"]+)['"]\s*\+""",
            html,
        )
        if not base_match:
            # Try alternate pattern without concatenation (static src assignment)
            static_match = re.search(
                r"""getElementById\(['"](cx-iframe|wp_player)['"]\)\.src\s*=\s*['"]([^'"]+)['"]""",
                html,
            )
            if static_match:
                src = static_match.group(2)
                if src.startswith("http"):
                    return src
            return None

        base_url = base_match.group(2)

        # Extract the first streamId from button onclick handlers
        # Pattern: changeStream('47356') or changeStream(47356)
        stream_match = re.search(
            r"""changeStream\(['"']?(\d+)['"']?\)""",
            html,
        )
        if not stream_match:
            # Try to find streamId from stream-btn elements
            soup = BeautifulSoup(html, "html.parser")
            for btn in soup.select("[id^='stream-btn-']"):
                btn_id = btn.get("id", "")
                stream_id = btn_id.replace("stream-btn-", "")
                if stream_id.isdigit():
                    return base_url + stream_id
            return None

        stream_id = stream_match.group(1)
        return base_url + stream_id
