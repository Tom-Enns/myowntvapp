"""TheTVApp.link stream backend — resolves streams from thetvapp.link event pages.

Similar to TheTVApp.to but different domain, URL structure, and embed host.
Uses gooz.aapmains.net iframe embeds with atob()-encoded stream URLs.
"""

import asyncio
import logging
import re
from base64 import b64decode
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from app.models import SportEvent, ResolvedStream
from app.backends.base import StreamBackend
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
        """Resolve a stream for the given event.

        If the event was created by this backend's schedule, the event_id
        contains the direct URL path. Otherwise, search by team names.
        """
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
        """Get URL if event_id points to thetvapp.link."""
        if event.event_id.startswith("thetvapplink:"):
            path = event.event_id.removeprefix("thetvapplink:")
            return f"https://thetvapp.link/{path}"
        return None

    async def _search_for_event(self, event: SportEvent) -> str | None:
        """Search thetvapp.link for an event matching by team names."""
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
                        # hrefs are already absolute on thetvapp.link
                        if href.startswith("http"):
                            return href
                        return f"https://thetvapp.link{href}"
        except Exception as e:
            logger.warning(f"[thetvapp.link] Search failed: {e}")

        return None

    async def _extract_stream(self, url: str) -> ResolvedStream | None:
        """Extract HLS stream URL from a thetvapp.link event page."""
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

            # Find the iframe (id="cx-iframe" or any iframe with embed URL)
            iframe_url = None
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "")
                if src and ("embed" in src.lower() or "stream" in src.lower()):
                    iframe_url = src if src.startswith("http") else urljoin(page_url, src)
                    break

            if not iframe_url:
                # Fallback: any iframe with an http src
                for iframe in soup.find_all("iframe"):
                    src = iframe.get("src", "")
                    if src and src.startswith("http") and "about:" not in src:
                        iframe_url = src
                        break

            if not iframe_url:
                raise RuntimeError("No stream embed iframe found on thetvapp.link page")

            # Fetch the iframe content
            async with session.get(
                iframe_url,
                headers={"User-Agent": _UA, "Referer": page_url},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as iframe_resp:
                if iframe_resp.status != 200:
                    raise RuntimeError(f"Iframe returned {iframe_resp.status}")
                iframe_html = await iframe_resp.text()

            stream_url = self._find_stream_in_html(iframe_html)
            if not stream_url:
                raise RuntimeError(
                    "No HLS stream found on thetvapp.link. The event may not have an active stream."
                )

            origin = iframe_url.split("/")[0] + "//" + iframe_url.split("/")[2]
            stream_headers = {"Referer": iframe_url, "Origin": origin}
            qualities = await parse_stream_qualities(stream_url, stream_headers)

            return ResolvedStream(
                backend_id=self.backend_id,
                backend_name=self.display_name,
                m3u8_url=stream_url,
                headers=stream_headers,
                qualities=qualities,
            )

    def _find_stream_in_html(self, html: str) -> str | None:
        """Parse HTML/JS to find HLS stream URLs — same strategies as thetvapp.to."""
        # Strategy 1: atob('...') pattern (base64-encoded URL)
        atob_matches = re.findall(r"atob\(['\"]([A-Za-z0-9+/=]+)['\"]\)", html)
        for match in atob_matches:
            try:
                decoded = b64decode(match).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                continue

        # Strategy 2: source: 'https://...' pattern
        src_match = re.search(r"source:\s*['\"]?(https?://[^'\"\s,]+)", html)
        if src_match:
            return src_match.group(1)

        # Strategy 3: Direct .m3u8 URL in JS
        m3u8_match = re.search(r"""['"](https?://[^'"]*\.m3u8[^'"]*)['"]""", html)
        if m3u8_match:
            return m3u8_match.group(1)

        # Strategy 4: URL containing 'playlist' and 'load'
        playlist_match = re.search(r"""['"](https?://[^'"]*playlist[^'"]*load[^'"]*)['"]""", html)
        if playlist_match:
            return playlist_match.group(1)

        # Strategy 5: URL containing '/playlist/'
        playlist_match2 = re.search(r"""['"](https?://[^'"]*/playlist/[^'"]*)['"]""", html)
        if playlist_match2:
            return playlist_match2.group(1)

        return None


def create_backend() -> TheTVAppLinkBackend:
    return TheTVAppLinkBackend()
