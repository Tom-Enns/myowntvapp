"""TheTVApp.link stream backend — resolves streams from thetvapp.link event pages.

Uses the shared site layer for stream extraction. This backend handles
game-finding (URL construction, category page searching) and delegates
the actual stream extraction to the TheTVApp site handler.
"""

import logging

import aiohttp
from bs4 import BeautifulSoup

from app.models import SportEvent, ResolvedStream, StreamLink
from app.backends.base import StreamBackend
from app.sites.registry import get_site_registry
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

        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            site_registry = get_site_registry()
            result = await site_registry.resolve(url, None, session)
            if not result:
                return None

            qualities = await parse_stream_qualities(result.m3u8_url, result.headers)

            return ResolvedStream(
                backend_id=self.backend_id,
                backend_name=self.display_name,
                m3u8_url=result.m3u8_url,
                headers=result.headers,
                qualities=qualities,
            )

    async def discover_links(self, event: SportEvent) -> list[StreamLink]:
        url = self._get_event_url(event)
        if not url:
            url = await self._search_for_event(event)
        if not url:
            return []
        return [StreamLink(
            url=url,
            backend_id=self.backend_id,
            backend_name=self.display_name,
        )]

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


def create_backend() -> TheTVAppLinkBackend:
    return TheTVAppLinkBackend()
