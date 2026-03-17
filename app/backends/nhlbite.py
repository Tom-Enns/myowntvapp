"""NHLBite.plus aggregator backend — scrapes multiple stream sources per game.

NHLBite is a directory/aggregator that lists ~8 stream links per NHL game
from different providers (streameast, thetvapp, sportsurge, etc.).
Each link goes to an external site with an iframe embed.

This backend:
1. Finds the game page by matching home team name to the URL slug
2. Scrapes the stream table for all external stream links
3. For each link, delegates to the site registry to auto-detect
   and extract the m3u8 URL (site-specific handling)
"""

import asyncio
import logging
import re
from urllib.parse import urljoin

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

BASE_URL = "https://nhlbite.plus"

# Max streams to resolve in parallel (avoid hammering too many sites at once)
MAX_PARALLEL_RESOLVES = 4


class NHLBiteBackend(StreamBackend):

    @property
    def backend_id(self) -> str:
        return "nhlbite"

    @property
    def display_name(self) -> str:
        return "NHLBite.plus"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        """Return the first working stream."""
        streams = await self.resolve_streams(event)
        return streams[0] if streams else None

    async def resolve_streams(self, event: SportEvent) -> list[ResolvedStream]:
        """Return ALL available streams for an NHL event."""
        if event.category.lower() != "nhl":
            return []

        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[nhlbite] Found game page: {game_url}")
        stream_links = await self._scrape_stream_table(game_url)
        if not stream_links:
            logger.info("[nhlbite] No stream links found on game page")
            return []

        logger.info(f"[nhlbite] Found {len(stream_links)} stream sources, resolving...")
        return await self._resolve_stream_links(stream_links, game_url)

    async def discover_links(self, event: SportEvent) -> list[StreamLink]:
        """Discover all external stream links for an NHL event."""
        if event.category.lower() != "nhl":
            return []
        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[nhlbite] Found game page: {game_url}")
        raw_links = await self._scrape_stream_table(game_url)
        if not raw_links:
            return []

        logger.info(f"[nhlbite] Discovered {len(raw_links)} stream links")
        links = []
        for link in raw_links:
            label = link["name"]
            if link["channel"]:
                label += f" ({link['channel']})"
            links.append(StreamLink(
                url=link["url"],
                backend_id=self.backend_id,
                backend_name=self.display_name,
                source_label=label,
                referer=game_url,
            ))
        return links

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    BASE_URL,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": _UA},
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _find_game_page(self, event: SportEvent) -> str | None:
        """Find the game page URL by matching team name on the homepage."""
        slug = _team_to_slug(event.home_team)
        if slug:
            # Direct URL construction: /{home-team-slug}-live-streaming-links
            return f"{BASE_URL}/{slug}-live-streaming-links"

        # Fallback: scrape homepage and match by team names
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    BASE_URL, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            home_lower = event.home_team.lower()

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if "live-streaming-links" in href:
                    text = link.get_text(strip=True).lower()
                    if home_lower in text or _fuzzy_team_match(home_lower, href):
                        if href.startswith("http"):
                            return href
                        return urljoin(BASE_URL, href)
        except Exception as e:
            logger.warning(f"[nhlbite] Homepage scrape failed: {e}")

        return None

    async def _scrape_stream_table(self, game_url: str) -> list[dict]:
        """Scrape the stream table from a game page.

        Returns list of dicts: {url, name, channel, language}
        """
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    game_url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[nhlbite] Game page returned {resp.status}")
                        return []
                    html = await resp.text()
        except Exception as e:
            logger.warning(f"[nhlbite] Failed to fetch game page: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        streams = []

        # Find stream rows in the .new-table
        for row in soup.select(".new-table .table-row"):
            watch_btn = row.select_one("a.watch-btn")
            if not watch_btn or not watch_btn.get("href"):
                continue

            url = watch_btn["href"]
            if not url.startswith("http"):
                url = urljoin(game_url, url)

            name_el = row.select_one(".streamer-name")
            channel_el = row.select_one(".badge-channel")
            lang_el = row.select_one(".badge-language")

            name = name_el.get_text(strip=True) if name_el else "Unknown"
            channel = channel_el.get_text(strip=True) if channel_el else ""
            language = lang_el.get_text(strip=True) if lang_el else ""

            streams.append({
                "url": url,
                "name": name,
                "channel": channel,
                "language": language,
            })

        return streams

    async def _resolve_stream_links(
        self, stream_links: list[dict], referer: str
    ) -> list[ResolvedStream]:
        """Resolve external stream links in parallel using the site registry."""
        site_registry = get_site_registry()
        results: list[ResolvedStream] = []
        semaphore = asyncio.Semaphore(MAX_PARALLEL_RESOLVES)

        async def _try_one(link: dict):
            async with semaphore:
                try:
                    stream = await self._resolve_single_link(
                        link, referer, site_registry
                    )
                    if stream:
                        results.append(stream)
                except Exception as e:
                    logger.debug(
                        f"[nhlbite] Failed to resolve {link['name']}: {e}"
                    )

        await asyncio.gather(*[_try_one(link) for link in stream_links])
        return results

    async def _resolve_single_link(
        self, link: dict, referer: str, site_registry
    ) -> ResolvedStream | None:
        """Follow an external stream link and resolve via the site registry."""
        url = link["url"]
        label = link["name"]
        if link["channel"]:
            label += f" ({link['channel']})"

        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            # Delegate to site registry — it auto-detects the right site handler
            result = await site_registry.resolve(url, referer, session)
            if not result:
                return None

            qualities = await parse_stream_qualities(result.m3u8_url, result.headers)

            return ResolvedStream(
                backend_id=self.backend_id,
                backend_name=self.display_name,
                m3u8_url=result.m3u8_url,
                headers=result.headers,
                qualities=qualities,
                source_label=label,
            )


def _team_to_slug(team_name: str) -> str | None:
    """Convert a team name like 'Colorado Avalanche' to 'colorado-avalanche'."""
    if not team_name:
        return None
    # Remove periods (e.g., "St. Louis") and convert to slug
    slug = team_name.lower().replace(".", "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def _fuzzy_team_match(team_lower: str, href: str) -> bool:
    """Check if a team name roughly matches a URL slug."""
    # Extract words from team name and check if they appear in the href
    words = team_lower.split()
    href_lower = href.lower()
    # At least the last word (usually the mascot) should match
    return len(words) > 0 and words[-1] in href_lower


def create_backend() -> NHLBiteBackend:
    return NHLBiteBackend()
