"""NFLWebHunter/Newserbir site handler — handles nflwebhunter.top embed pages.

NFLWebHunter is an intermediary iframe host used by totalsportekarmy.com
(weakstreams, crackstreams, soccerstreams, hesgoal). The chain is:

  totalsportekarmy.com → iframe to nflwebhunter.top/channel{N}
                       → iframe to newserbir.site/player_stateless/channel{N}
                       → HLS.js player with m3u8 + JWT token

The player page at newserbir.site serves the m3u8 URL and token as plain
JavaScript variables — no obfuscation. It also has a token refresh API:
  POST newserbir.site/api/refresh_token_stateless.php
  Body: {channel_slug, stream_path}

This handler follows the iframe chain with correct Referer headers and
extracts the m3u8 URL from the final player page.
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


class NFLWebHunterSite(StreamSite):
    """Handles stream extraction from nflwebhunter.top iframe embeds."""

    @property
    def site_id(self) -> str:
        return "nflwebhunter"

    @property
    def domains(self) -> list[str]:
        return [
            "nflwebhunter.top",
        ]

    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        # Step 1: Fetch the nflwebhunter page to find the newserbir iframe
        player_url = await self._find_player_iframe(url, referer, session)
        if not player_url:
            return None

        # Step 2: Fetch the newserbir player page to extract m3u8 URL
        return await self._extract_from_player(player_url, url, session)

    async def _find_player_iframe(self, url: str, referer: str | None,
                                   session: aiohttp.ClientSession) -> str | None:
        """Fetch nflwebhunter page and find the newserbir player iframe."""
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[nflwebhunter] Page returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.debug(f"[nflwebhunter] Failed to fetch {url[:80]}: {e}")
            return None

        # Find the newserbir player iframe
        # Pattern: <iframe src="https://lob1.newserbir.site/player_stateless/channel14">
        iframe_match = re.search(
            r'<iframe[^>]*src=["\'](\s*https?://[^"\']*newserbir[^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        if iframe_match:
            return iframe_match.group(1).strip()

        # Fallback: any iframe with 'player' in the URL
        player_match = re.search(
            r'<iframe[^>]*src=["\'](\s*https?://[^"\']*player[^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        if player_match:
            return player_match.group(1).strip()

        logger.debug(f"[nflwebhunter] No player iframe found on {url[:80]}")
        return None

    async def _extract_from_player(self, player_url: str, referer: str,
                                    session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch the newserbir player page and extract the m3u8 URL."""
        headers = {
            "User-Agent": _UA,
            "Referer": referer,
        }

        try:
            async with session.get(
                player_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[nflwebhunter] Player returned {resp.status}: {player_url[:80]}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.debug(f"[nflwebhunter] Failed to fetch player: {e}")
            return None

        # Extract currentStreamUrl from the JavaScript
        # Pattern: let currentStreamUrl = "https://fg1.fgfbg5433dd.site/simpch14/index.m3u8?token=...";
        stream_match = re.search(
            r'(?:let|var|const)\s+currentStreamUrl\s*=\s*["\']([^"\']+)["\']',
            html,
        )
        if stream_match:
            m3u8_url = stream_match.group(1).replace(r'\/', '/')
            if m3u8_url.startswith("http"):
                logger.info(f"[nflwebhunter] Found stream URL from currentStreamUrl")
                return ResolvedEmbed(
                    m3u8_url=m3u8_url,
                    headers={"Referer": player_url, "Origin": _extract_origin(player_url)},
                )

        # Fallback: look for any m3u8 URL in the page
        m3u8_match = re.search(r"""['"](\s*https?://[^'"]*\.m3u8[^'"]*)['"]""", html)
        if m3u8_match:
            m3u8_url = m3u8_match.group(1).strip().replace(r'\/', '/')
            logger.info(f"[nflwebhunter] Found stream URL from m3u8 pattern")
            return ResolvedEmbed(
                m3u8_url=m3u8_url,
                headers={"Referer": player_url, "Origin": _extract_origin(player_url)},
            )

        logger.debug(f"[nflwebhunter] No m3u8 URL found in player page")
        return None


def _extract_origin(url: str) -> str:
    """Extract the origin (scheme + host) from a URL."""
    parts = url.split("/")
    if len(parts) >= 3:
        return parts[0] + "//" + parts[2]
    return url
