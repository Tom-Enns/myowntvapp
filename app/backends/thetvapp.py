"""TheTVApp.to stream backend — resolves streams from thetvapp.to event pages."""

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


class TheTVAppBackend(StreamBackend):

    @property
    def backend_id(self) -> str:
        return "thetvapp"

    @property
    def display_name(self) -> str:
        return "TheTVApp.to"

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        """Resolve a stream for the given event.

        If the event was created by the TheTVApp schedule provider, the event_id
        contains the slug needed to construct the URL. Otherwise, we search thetvapp.to
        for a matching event by team names.
        """
        url = self._get_event_url(event)
        if not url:
            # Try to find a matching event on thetvapp.to by searching the category
            url = await self._search_for_event(event)

        if not url:
            return None

        logger.info(f"Extracting stream from: {url}")
        return await self._extract_stream(url)

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://thetvapp.to", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def _get_event_url(self, event: SportEvent) -> str | None:
        """Get URL if this event was created by the TheTVApp schedule provider."""
        if event.event_id.startswith("thetvapp:"):
            slug = event.event_id.removeprefix("thetvapp:")
            if event.category == "tv":
                return f"https://thetvapp.to/tv/{slug}/"
            return f"https://thetvapp.to/event/{slug}/"
        return None

    async def _search_for_event(self, event: SportEvent) -> str | None:
        """Search thetvapp.to for an event matching by team names."""
        if not event.home_team or not event.away_team:
            return None

        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                url = f"https://thetvapp.to/{event.category.lower()}"
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
                        return f"https://thetvapp.to{href}"
        except Exception as e:
            logger.warning(f"Search failed: {e}")

        return None

    async def _extract_stream(self, url: str) -> ResolvedStream | None:
        """Extract HLS stream URL from a thetvapp.to event page."""
        headers = {"User-Agent": _UA}

        try:
            session = aiohttp.ClientSession(headers=headers)
        except Exception as e:
            raise RuntimeError(f"Failed to create HTTP session: {e}")

        async with session:
            try:
                resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=15))
            except aiohttp.ClientConnectorError:
                raise ConnectionError(
                    "Cannot connect to TheTVApp.to — the site may be down or blocked by your network."
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    "TheTVApp.to did not respond in time. The site may be overloaded."
                )

            async with resp:
                if resp.status == 403:
                    raise PermissionError(
                        "TheTVApp.to returned 403 Forbidden. "
                        "Your IP address appears to be blocked by this provider."
                    )
                if resp.status == 451:
                    raise PermissionError(
                        "TheTVApp.to returned 451 Unavailable For Legal Reasons. "
                        "This content may be geo-restricted in your region."
                    )
                if resp.status != 200:
                    raise RuntimeError(
                        f"TheTVApp.to returned HTTP {resp.status}. The site may be experiencing issues."
                    )
                page_html = await resp.text()
                page_url = str(resp.url)
                page_cookies = {k: v.value for k, v in resp.cookies.items()}

            soup = BeautifulSoup(page_html, "html.parser")

            # TV channel (JWPlayer with token endpoint)
            stream_name_div = soup.find(id="stream_name")
            if stream_name_div and stream_name_div.get("name"):
                return await self._extract_tv_channel(session, page_url, page_cookies, stream_name_div["name"])

            # iframe-based sports stream
            return await self._extract_iframe_stream(session, soup, page_url)

    async def _extract_tv_channel(self, session: aiohttp.ClientSession,
                                   page_url: str, cookies: dict, stream_name: str) -> ResolvedStream:
        origin = page_url.split("/")[0] + "//" + page_url.split("/")[2]
        token_url = f"{origin}/token/{stream_name}"

        async with session.get(token_url, headers={"User-Agent": _UA, "Referer": page_url},
                               cookies=cookies, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Token endpoint returned {resp.status}")
            data = await resp.json()

        m3u8_url = data.get("url")
        if not m3u8_url:
            raise RuntimeError("Token endpoint did not return a stream URL")

        stream_headers = {"Referer": page_url, "Origin": origin}
        qualities = await parse_stream_qualities(m3u8_url, stream_headers)

        return ResolvedStream(
            backend_id=self.backend_id,
            backend_name=self.display_name,
            m3u8_url=m3u8_url,
            headers=stream_headers,
            qualities=qualities,
        )

    async def _extract_iframe_stream(self, session: aiohttp.ClientSession,
                                      soup: BeautifulSoup, page_url: str) -> ResolvedStream:
        iframe_url = None
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if src and "embed" in src.lower():
                iframe_url = src if src.startswith("http") else urljoin(page_url, src)
                break

        if not iframe_url:
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "")
                if src and src.startswith("http") and "about:" not in src:
                    iframe_url = src
                    break

        if not iframe_url:
            raise RuntimeError("No stream embed iframe found on page")

        async with session.get(iframe_url, headers={"User-Agent": _UA, "Referer": page_url},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Iframe returned {resp.status}")
            iframe_html = await resp.text()

        stream_url = self._find_stream_in_html(iframe_html)
        if not stream_url:
            raise RuntimeError("No HLS stream found. The page may not have an active stream right now.")

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
        """Parse HTML/JS to find HLS stream URLs."""
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


def create_backend() -> TheTVAppBackend:
    return TheTVAppBackend()
