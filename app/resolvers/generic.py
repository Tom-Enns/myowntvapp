"""Generic stream resolver — tries multiple extraction strategies on any embed page.

This handles the most common patterns found across streaming sites:
  1. atob() base64-encoded URLs
  2. source: 'url' assignments
  3. Direct .m3u8 URLs in JavaScript
  4. /playlist/ URL patterns
"""

import logging
import re
from base64 import b64decode

import aiohttp

from app.resolvers.base import StreamResolver, ResolvedEmbed

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class GenericResolver(StreamResolver):
    """Fallback resolver that tries common extraction patterns on any embed URL."""

    @property
    def resolver_id(self) -> str:
        return "generic"

    @property
    def domains(self) -> list[str]:
        return []  # Empty = fallback resolver

    async def resolve(self, url: str, referer: str,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        try:
            async with session.get(
                url,
                headers={"User-Agent": _UA, "Referer": referer},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[generic] Embed returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.warning(f"[generic] Failed to fetch embed: {e}")
            return None

        m3u8_url = find_stream_in_html(html)
        if not m3u8_url:
            return None

        origin = url.split("/")[0] + "//" + url.split("/")[2]
        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": url, "Origin": origin},
        )


def find_stream_in_html(html: str) -> str | None:
    """Parse HTML/JS to find HLS stream URLs using multiple strategies.

    This is the shared extraction logic used by the generic resolver
    and available to any resolver that needs common pattern matching.
    """
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
