"""NBABite aggregator backend — scrapes multiple stream sources per NBA game.

NBABite (nbabite.is) is a directory/aggregator that lists stream links per
NBA game from different providers. Unlike NHLBite, it uses a Bootstrap table
with hidden <input> fields containing stream URLs.

This backend:
1. Scrapes the homepage for game links matching team names
2. Scrapes the stream table for all external stream links
3. For each link, delegates to the site registry for resolution
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

BASE_URL = "https://nbabite.is"

MAX_PARALLEL_RESOLVES = 4


class NBABiteBackend(StreamBackend):

    @property
    def backend_id(self) -> str:
        return "nbabite"

    @property
    def display_name(self) -> str:
        return "NBABite"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        streams = await self.resolve_streams(event)
        return streams[0] if streams else None

    async def resolve_streams(self, event: SportEvent) -> list[ResolvedStream]:
        if event.category.lower() != "nba":
            return []
        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[nbabite] Found game page: {game_url}")
        stream_links = await self._scrape_stream_table(game_url)
        if not stream_links:
            logger.info("[nbabite] No stream links found on game page")
            return []

        logger.info(f"[nbabite] Found {len(stream_links)} stream sources, resolving...")
        return await self._resolve_stream_links(stream_links, game_url)

    async def discover_links(self, event: SportEvent) -> list[StreamLink]:
        if event.category.lower() != "nba":
            return []
        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[nbabite] Found game page: {game_url}")
        raw_links = await self._scrape_stream_table(game_url)
        if not raw_links:
            return []

        logger.info(f"[nbabite] Discovered {len(raw_links)} stream links")
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
                    allow_redirects=True,
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _find_game_page(self, event: SportEvent) -> str | None:
        """Find the game page URL by scraping the homepage for matching teams.

        NBABite URLs use /{Team1}-vs-{Team2}/{ID} pattern — we can't construct
        these directly, so we must scrape the homepage.
        """
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    BASE_URL,
                    timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
                    final_url = str(resp.url)
        except Exception as e:
            logger.warning(f"[nbabite] Homepage fetch failed: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        home_lower = event.home_team.lower()
        away_lower = (event.away_team or "").lower()

        # Look for game cards with team names
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # NBABite game URLs contain "-vs-"
            if "-vs-" not in href.lower():
                continue

            # Check if team names appear in the link text or href
            text = link.get_text(strip=True).lower()
            href_lower = href.lower()

            home_match = _fuzzy_team_match(home_lower, text) or _fuzzy_team_match(home_lower, href_lower)
            away_match = not away_lower or _fuzzy_team_match(away_lower, text) or _fuzzy_team_match(away_lower, href_lower)

            if home_match and away_match:
                if href.startswith("http"):
                    return href
                # Use the final URL base (after redirect) for relative URLs
                return urljoin(final_url, href)

        return None

    async def _scrape_stream_table(self, game_url: str) -> list[dict]:
        """Scrape the stream table from a game page.

        NBABite uses a Bootstrap table with hidden <input> fields for URLs,
        not <a href> links like NHLBite.
        """
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    game_url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[nbabite] Game page returned {resp.status}")
                        return []
                    html = await resp.text()
        except Exception as e:
            logger.warning(f"[nbabite] Failed to fetch game page: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        streams = []

        # NBABite uses table.table with tr#tr-round rows
        for row in soup.select("table.table tr#tr-round, table.table tr.bg-light-gray"):
            # Stream URL is in a hidden input field
            hidden_input = row.find("input", {"type": "hidden"})
            if not hidden_input or not hidden_input.get("value"):
                continue

            url = hidden_input["value"]
            if not url.startswith("http"):
                continue

            # Extract data from table cells
            cells = row.find_all("td")
            if len(cells) < 7:
                continue

            name = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
            # Quality is in cell 3, language in cell 4, channel in cell 6
            channel = cells[6].get_text(strip=True) if len(cells) > 6 else ""
            language = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            streams.append({
                "url": url,
                "name": name,
                "channel": channel,
                "language": language,
            })

        # Also check for NHLBite-style table (in case they share structure)
        if not streams:
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

                streams.append({
                    "url": url,
                    "name": name_el.get_text(strip=True) if name_el else "Unknown",
                    "channel": channel_el.get_text(strip=True) if channel_el else "",
                    "language": lang_el.get_text(strip=True) if lang_el else "",
                })

        return streams

    async def _resolve_stream_links(
        self, stream_links: list[dict], referer: str
    ) -> list[ResolvedStream]:
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
                    logger.debug(f"[nbabite] Failed to resolve {link['name']}: {e}")

        await asyncio.gather(*[_try_one(link) for link in stream_links])
        return results

    async def _resolve_single_link(
        self, link: dict, referer: str, site_registry
    ) -> ResolvedStream | None:
        url = link["url"]
        label = link["name"]
        if link["channel"]:
            label += f" ({link['channel']})"

        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
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


def _fuzzy_team_match(team_lower: str, text: str) -> bool:
    """Check if a team name roughly matches text or a URL."""
    words = team_lower.split()
    if not words:
        return False
    # The last word is usually the mascot (e.g., "Lakers", "Celtics")
    # Check if it appears in the text
    return words[-1] in text.lower()


def create_backend() -> NBABiteBackend:
    return NBABiteBackend()
