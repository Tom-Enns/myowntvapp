"""MLBBite aggregator backend — scrapes multiple stream sources per MLB game.

MLBBite (mlbbite.plus) is a directory/aggregator that lists stream links per
MLB game from different providers. Uses the same CSS classes as NHLBite
(.new-table, .table-row, .watch-btn) but different URL patterns.

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

BASE_URL = "https://mlbbite.plus"

MAX_PARALLEL_RESOLVES = 4


class MLBBiteBackend(StreamBackend):

    @property
    def backend_id(self) -> str:
        return "mlbbite"

    @property
    def display_name(self) -> str:
        return "MLBBite"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        streams = await self.resolve_streams(event)
        return streams[0] if streams else None

    async def resolve_streams(self, event: SportEvent) -> list[ResolvedStream]:
        if event.category.lower() != "mlb":
            return []
        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[mlbbite] Found game page: {game_url}")
        stream_links = await self._scrape_stream_table(game_url)
        if not stream_links:
            logger.info("[mlbbite] No stream links found on game page")
            return []

        logger.info(f"[mlbbite] Found {len(stream_links)} stream sources, resolving...")
        return await self._resolve_stream_links(stream_links, game_url)

    async def discover_links(self, event: SportEvent) -> list[StreamLink]:
        if event.category.lower() != "mlb":
            return []
        if not event.home_team:
            return []

        game_url = await self._find_game_page(event)
        if not game_url:
            return []

        logger.info(f"[mlbbite] Found game page: {game_url}")
        raw_links = await self._scrape_stream_table(game_url)
        if not raw_links:
            return []

        logger.info(f"[mlbbite] Discovered {len(raw_links)} stream links")
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
        """Find the game page by scraping the homepage for matching teams.

        MLBBite URLs use /watch/live/{away}-at-{home}-{id}-free-live-stream
        which includes a numeric ID, so we must scrape the homepage.
        """
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    BASE_URL, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
        except Exception as e:
            logger.warning(f"[mlbbite] Homepage fetch failed: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        home_lower = event.home_team.lower()
        away_lower = (event.away_team or "").lower()

        # Look for game links — MLBBite uses /watch/live/ pattern
        for link in soup.find_all("a", href=True):
            href = link["href"]
            href_lower = href.lower()

            # Match links containing team slugs
            if "/watch/live/" not in href_lower and "free-live-stream" not in href_lower:
                continue

            text = link.get_text(strip=True).lower()

            home_match = _fuzzy_team_match(home_lower, text) or _fuzzy_team_match(home_lower, href_lower)
            away_match = not away_lower or _fuzzy_team_match(away_lower, text) or _fuzzy_team_match(away_lower, href_lower)

            if home_match and away_match:
                if href.startswith("http"):
                    return href
                return urljoin(BASE_URL, href)

        # Fallback: try team page pattern /{home-team-slug}-live-streaming
        slug = _team_to_slug(event.home_team)
        if slug:
            # Try direct URL like NHLBite
            team_url = f"{BASE_URL}/{slug}-live-streaming-links"
            try:
                async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                    async with session.get(
                        team_url, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            return team_url
            except Exception:
                pass

        return None

    async def _scrape_stream_table(self, game_url: str) -> list[dict]:
        """Scrape the stream table from a game page.

        MLBBite uses the same CSS classes as NHLBite (.new-table, .table-row).
        Also tries the NBABite-style hidden input approach as fallback.
        """
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(
                    game_url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[mlbbite] Game page returned {resp.status}")
                        return []
                    html = await resp.text()
        except Exception as e:
            logger.warning(f"[mlbbite] Failed to fetch game page: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        streams = []

        # Try NHLBite-style table first (confirmed same CSS in MLBBite)
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

        # Fallback: try NBABite-style hidden inputs
        if not streams:
            for row in soup.select("table.table tr"):
                hidden_input = row.find("input", {"type": "hidden"})
                if not hidden_input or not hidden_input.get("value"):
                    continue

                url = hidden_input["value"]
                if not url.startswith("http"):
                    continue

                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                name = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                channel = cells[6].get_text(strip=True) if len(cells) > 6 else ""
                language = cells[4].get_text(strip=True) if len(cells) > 4 else ""

                streams.append({
                    "url": url,
                    "name": name,
                    "channel": channel,
                    "language": language,
                })

        # Fallback: extract URLs from onclick handlers
        if not streams:
            for row in soup.select("tr[onclick]"):
                onclick = row.get("onclick", "")
                url_match = re.search(r"window\.open\(['\"]([^'\"]+)['\"]", onclick)
                if url_match:
                    url = url_match.group(1)
                    if url.startswith("http"):
                        cells = row.find_all("td")
                        name = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                        streams.append({
                            "url": url,
                            "name": name,
                            "channel": "",
                            "language": "",
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
                    logger.debug(f"[mlbbite] Failed to resolve {link['name']}: {e}")

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


def _team_to_slug(team_name: str) -> str | None:
    if not team_name:
        return None
    slug = team_name.lower().replace(".", "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def _fuzzy_team_match(team_lower: str, text: str) -> bool:
    words = team_lower.split()
    if not words:
        return False
    # The last word is usually the mascot
    return words[-1] in text.lower()


def create_backend() -> MLBBiteBackend:
    return MLBBiteBackend()
