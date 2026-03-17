"""StreamEast site handler — handles istreameast.app stream pages.

StreamEast game pages have an iframe (#wp_player) pointing to an embed
at gooz.aapmains.net. The embed uses Clappr player with a base64-encoded
m3u8 URL via atob(). The generic resolver already handles the atob pattern,
so this site handler just needs to find the iframe.
"""

import logging

import aiohttp
from bs4 import BeautifulSoup

from app.resolvers.base import ResolvedEmbed
from app.resolvers.registry import get_resolver_registry
from app.sites.base import StreamSite
from app.sites.generic import find_iframe_url

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class StreamEastSite(StreamSite):
    """Handles stream extraction from StreamEast (istreameast.app) pages."""

    @property
    def site_id(self) -> str:
        return "streameast"

    @property
    def domains(self) -> list[str]:
        return [
            "istreameast.app",
            "streameast.center",
            "streameast100.is",
            "gostreameast.link",
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
                    logger.debug(f"[streameast] Page returned {resp.status}: {url[:80]}")
                    return None
                page_html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[streameast] Failed to fetch {url[:80]}: {e}")
            return None

        # StreamEast uses iframe#wp_player for the embed
        soup = BeautifulSoup(page_html, "html.parser")

        # Try the specific wp_player iframe first
        wp_iframe = soup.find("iframe", id="wp_player")
        if wp_iframe and wp_iframe.get("src") and wp_iframe["src"].startswith("http"):
            iframe_url = wp_iframe["src"]
        else:
            # Fall back to generic iframe finding
            iframe_url = find_iframe_url(page_html, page_url)

        if not iframe_url:
            logger.debug(f"[streameast] No iframe found on {url[:80]}")
            return None

        # Resolve the embed (gooz.aapmains.net) — generic resolver handles atob()
        resolver_registry = get_resolver_registry()
        return await resolver_registry.resolve(iframe_url, page_url, session)
