"""TotalSportek site handler — handles totalsportekarmy.com stream pages.

TotalSportek (live3/live4/live5/live6.totalsportekarmy.com) is one of the
most common stream sources linked from NBABite/NHLBite. The stream sources
listed as "weakstreams", "CrackStreams", "SoccerStreams", "Hesgoal Streams"
all point to totalsportekarmy.com subdomains.

The page contains an iframe to nflwebhunter.top/channel{N}, which this
handler extracts and delegates to the NFLWebHunterSite for resolution.
"""

import logging
import re

import aiohttp

from app.resolvers.base import ResolvedEmbed
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class TotalSportekSite(StreamSite):
    """Handles stream extraction from TotalSportek pages."""

    @property
    def site_id(self) -> str:
        return "totalsportek"

    @property
    def domains(self) -> list[str]:
        return [
            "totalsportekarmy.com",
            "live3.totalsportekarmy.com",
            "live4.totalsportekarmy.com",
            "live5.totalsportekarmy.com",
            "live6.totalsportekarmy.com",
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
                    logger.debug(f"[totalsportek] Page returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[totalsportek] Failed to fetch {url[:80]}: {e}")
            return None

        # Find the nflwebhunter iframe (or similar player embed)
        iframe_url = self._find_player_iframe(html)
        if not iframe_url:
            logger.debug(f"[totalsportek] No player iframe found on {url[:80]}")
            return None

        # Delegate to the site registry to handle the iframe
        from app.sites.registry import get_site_registry
        site_registry = get_site_registry()
        return await site_registry.resolve(iframe_url, page_url, session)

    def _find_player_iframe(self, html: str) -> str | None:
        """Find the player iframe URL in the page HTML."""
        # Pattern 1: nflwebhunter iframe (most common)
        hunter_match = re.search(
            r'<iframe[^>]*src=["\'](\s*https?://[^"\']*nflwebhunter[^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        if hunter_match:
            return hunter_match.group(1).strip()

        # Pattern 2: Any iframe with 'channel' in the URL
        channel_match = re.search(
            r'<iframe[^>]*src=["\'](\s*https?://[^"\']*channel\d+[^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        if channel_match:
            return channel_match.group(1).strip()

        # Pattern 3: Any iframe that's not youtube/ads
        for match in re.finditer(
            r'<iframe[^>]*src=["\'](\s*https?://[^"\']+)["\']',
            html,
            re.IGNORECASE,
        ):
            src = match.group(1).strip()
            skip = ("youtube.com", "google.com", "facebook.com", "twitter.com",
                    "histats.com", "cloudflare")
            if not any(s in src.lower() for s in skip):
                return src

        return None
