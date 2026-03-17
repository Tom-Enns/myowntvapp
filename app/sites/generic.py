"""Generic site handler — fetches a page, finds an iframe, delegates to resolver registry.

This is the fallback site handler. It works for any site that has a
straightforward iframe embed pointing to a stream player. For sites
that need special handling (login, cookies, JS-rendered iframes, token
endpoints), create a site-specific handler instead.
"""

import logging
import re
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from app.resolvers.base import ResolvedEmbed
from app.resolvers.registry import get_resolver_registry
from app.resolvers.generic import find_stream_in_html
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class GenericSite(StreamSite):
    """Fallback site handler that works for simple iframe-based streaming pages."""

    @property
    def site_id(self) -> str:
        return "generic"

    @property
    def domains(self) -> list[str]:
        return []  # Empty = fallback

    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        try:
            headers = {"User-Agent": _UA}
            if referer:
                headers["Referer"] = referer
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[generic-site] Page returned {resp.status}: {url[:80]}")
                    return None
                page_html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[generic-site] Failed to fetch {url[:80]}: {e}")
            return None

        # Try to find an iframe and resolve it
        iframe_url = find_iframe_url(page_html, page_url)
        if iframe_url:
            resolver_registry = get_resolver_registry()
            return await resolver_registry.resolve(iframe_url, page_url, session)

        # Fallback: look for m3u8 directly in the page HTML/JS
        m3u8_url = find_stream_in_html(page_html)
        if m3u8_url:
            origin = page_url.split("/")[0] + "//" + page_url.split("/")[2]
            return ResolvedEmbed(
                m3u8_url=m3u8_url,
                headers={"Referer": page_url, "Origin": origin},
            )

        return None


def find_iframe_url(html: str, page_url: str) -> str | None:
    """Find an embed iframe URL on a page.

    This is the shared iframe-finding logic used by the generic site handler
    and available to any site that needs it. Checks for common patterns:
      1. cx-iframe (thetvapp-style sites)
      2. Iframes with 'embed' or 'stream' in src
      3. Any http iframe (excluding about:)
      4. JavaScript cx-iframe src assignment
    """
    soup = BeautifulSoup(html, "html.parser")

    # Check for cx-iframe first (common on thetvapp-style sites)
    cx = soup.find("iframe", id="cx-iframe")
    if cx and cx.get("src") and cx["src"].startswith("http"):
        return cx["src"]

    # Iframes with 'embed' or 'stream' in src
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and ("embed" in src.lower() or "stream" in src.lower()):
            return src if src.startswith("http") else urljoin(page_url, src)

    # Any http iframe
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and src.startswith("http") and "about:" not in src:
            return src

    # Some sites inject iframe via JS: look for cx-iframe src assignment
    script_match = re.search(
        r"""getElementById\(['"]cx-iframe['"]\)\.src\s*=\s*['"]([^'"]+)['"]""",
        html,
    )
    if script_match:
        iframe_src = script_match.group(1)
        if iframe_src.startswith("http"):
            return iframe_src

    return None
