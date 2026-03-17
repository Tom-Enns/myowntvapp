"""TheTVApp site handler — handles thetvapp.to and thetvapp.link stream pages.

Knows about the site-specific structure:
  - JWPlayer token endpoint for TV channels (stream_name div)
  - Standard iframe embed for sports events
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


class TheTVAppSite(StreamSite):
    """Handles stream extraction from thetvapp.to and thetvapp.link pages."""

    @property
    def site_id(self) -> str:
        return "thetvapp"

    @property
    def domains(self) -> list[str]:
        return ["thetvapp.to", "thetvapp.link"]

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
                    logger.warning(f"[thetvapp-site] Page returned {resp.status}: {url[:80]}")
                    return None
                page_html = await resp.text()
                page_url = str(resp.url)
                page_cookies = {k: v.value for k, v in resp.cookies.items()}
        except Exception as e:
            logger.warning(f"[thetvapp-site] Failed to fetch {url[:80]}: {e}")
            return None

        soup = BeautifulSoup(page_html, "html.parser")

        # TV channel path: JWPlayer with token endpoint
        stream_name_div = soup.find(id="stream_name")
        if stream_name_div and stream_name_div.get("name"):
            return await self._resolve_tv_channel(
                session, page_url, page_cookies, stream_name_div["name"]
            )

        # Sports path: find iframe and use resolver registry
        iframe_url = find_iframe_url(page_html, page_url)
        if not iframe_url:
            logger.debug(f"[thetvapp-site] No iframe found on {url[:80]}")
            return None

        resolver_registry = get_resolver_registry()
        return await resolver_registry.resolve(iframe_url, page_url, session)

    async def _resolve_tv_channel(
        self, session: aiohttp.ClientSession,
        page_url: str, cookies: dict, stream_name: str
    ) -> ResolvedEmbed | None:
        """Extract stream via the JWPlayer token endpoint (TV channels only)."""
        origin = page_url.split("/")[0] + "//" + page_url.split("/")[2]
        token_url = f"{origin}/token/{stream_name}"

        try:
            async with session.get(
                token_url,
                headers={"User-Agent": _UA, "Referer": page_url},
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[thetvapp-site] Token endpoint returned {resp.status}")
                    return None
                data = await resp.json()
        except Exception as e:
            logger.warning(f"[thetvapp-site] Token request failed: {e}")
            return None

        m3u8_url = data.get("url")
        if not m3u8_url:
            return None

        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": page_url, "Origin": origin},
        )
