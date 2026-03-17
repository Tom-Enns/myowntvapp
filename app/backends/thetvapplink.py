"""TheTVApp.link stream backend — resolves streams from thetvapp.link event pages.

Similar to TheTVApp.to but different domain, URL structure, and embed host.
Uses the shared resolver registry for iframe stream extraction.
"""

import asyncio
import logging
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from app.models import SportEvent, ResolvedStream
from app.backends.base import StreamBackend
from app.resolvers.registry import get_resolver_registry
from app.services.quality import parse_stream_qualities

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Maps our internal categories to thetvapp.link URL paths
CATEGORY_PATHS = {
    "nba": "nbastreams",
    "mlb": "mlbstreams",
    "nhl": "nhlstreams",
    "nfl": "nflstreams",
    "soccer": "soccerstreams",
    "ncaaf": "cfbstreams",
    "ncaab": "ncaastreams",
}


class TheTVAppLinkBackend(StreamBackend):

    @property
    def backend_id(self) -> str:
        return "thetvapplink"

    @property
    def display_name(self) -> str:
        return "TheTVApp.link"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        url = self._get_event_url(event)
        if not url:
            url = await self._search_for_event(event)

        if not url:
            return None

        logger.info(f"[thetvapp.link] Extracting stream from: {url}")
        return await self._extract_stream(url)

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://thetvapp.link",
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": _UA},
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def _get_event_url(self, event: SportEvent) -> str | None:
        if event.event_id.startswith("thetvapplink:"):
            path = event.event_id.removeprefix("thetvapplink:")
            return f"https://thetvapp.link/{path}"
        return None

    async def _search_for_event(self, event: SportEvent) -> str | None:
        if not event.home_team or not event.away_team:
            return None

        cat_path = CATEGORY_PATHS.get(event.category.lower())
        if not cat_path:
            return None

        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                url = f"https://thetvapp.link/{cat_path}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            home_lower = event.home_team.lower()
            away_lower = event.away_team.lower()

            for item in soup.find_all("a", class_="list-group-item"):
                text = item.text.strip().lower()
                if home_lower in text and away_lower in text:
                    href = item.get("href")
                    if href:
                        if href.startswith("http"):
                            return href
                        return f"https://thetvapp.link{href}"
        except Exception as e:
            logger.warning(f"[thetvapp.link] Search failed: {e}")

        return None

    async def _extract_stream(self, url: str) -> ResolvedStream | None:
        headers = {"User-Agent": _UA}

        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=15))
            except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError):
                raise ConnectionError(
                    "Cannot connect to TheTVApp.link — the site may be down or blocked."
                )
            except asyncio.TimeoutError:
                raise TimeoutError("TheTVApp.link did not respond in time.")

            async with resp:
                if resp.status == 403:
                    raise PermissionError(
                        "TheTVApp.link returned 403. Your IP may be blocked."
                    )
                if resp.status != 200:
                    raise RuntimeError(f"TheTVApp.link returned HTTP {resp.status}.")
                page_html = await resp.text()
                page_url = str(resp.url)

            soup = BeautifulSoup(page_html, "html.parser")

            # Find the iframe
            iframe_url = _find_iframe_url(soup, page_url)
            if not iframe_url:
                raise RuntimeError("No stream embed iframe found on thetvapp.link page")

            # Use resolver registry for auto-detection
            resolver_registry = get_resolver_registry()
            result = await resolver_registry.resolve(iframe_url, page_url, session)
            if not result:
                raise RuntimeError(
                    "No HLS stream found on thetvapp.link. The event may not have an active stream."
                )

            qualities = await parse_stream_qualities(result.m3u8_url, result.headers)

            return ResolvedStream(
                backend_id=self.backend_id,
                backend_name=self.display_name,
                m3u8_url=result.m3u8_url,
                headers=result.headers,
                qualities=qualities,
            )


def _find_iframe_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """Find the embed iframe URL on a page."""
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and ("embed" in src.lower() or "stream" in src.lower()):
            return src if src.startswith("http") else urljoin(page_url, src)

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and src.startswith("http") and "about:" not in src:
            return src

    return None


def create_backend() -> TheTVAppLinkBackend:
    return TheTVAppLinkBackend()
