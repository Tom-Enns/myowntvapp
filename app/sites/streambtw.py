"""StreamBTW site handler — handles streambtw.com embed pages.

StreamBTW embeds use Clappr player with a base64-encoded (and reversed)
m3u8 URL. The pattern is:
  var encoded = "...base64...";
  encoded = encoded.split("").reverse().join("");
  var server = atob(encoded.split("").reverse().join(""));

The double-reverse cancels out, so we just need to extract the original
base64 string and decode it directly. The result is an m3u8 URL at
streambtw.com/playlist/.

These pages are typically loaded as iframes from aggregator sites.
"""

import logging
import re
from base64 import b64decode

import aiohttp

from app.resolvers.base import ResolvedEmbed
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class StreamBTWSite(StreamSite):
    """Handles stream extraction from StreamBTW (streambtw.com) pages."""

    @property
    def site_id(self) -> str:
        return "streambtw"

    @property
    def domains(self) -> list[str]:
        return ["streambtw.com"]

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
                    logger.debug(f"[streambtw] Page returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.debug(f"[streambtw] Failed to fetch {url[:80]}: {e}")
            return None

        m3u8_url = self._extract_stream_url(html)
        if not m3u8_url:
            logger.debug(f"[streambtw] No stream URL found on {url[:80]}")
            return None

        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": url, "Origin": "https://streambtw.com"},
        )

    def _extract_stream_url(self, html: str) -> str | None:
        """Extract m3u8 URL from the reversed-base64 pattern.

        The JS does:
          var encoded = "BASE64STRING";
          encoded = encoded.split("").reverse().join("");
          var server = atob(encoded.split("").reverse().join(""));

        The two reverses cancel out, so atob(original) gives the URL.
        We also try a direct atob match and plain m3u8 URL as fallbacks.
        """
        # Pattern 1: var encoded = "..." (the Clappr atob-reverse pattern)
        encoded_match = re.search(
            r'var\s+encoded\s*=\s*["\']([A-Za-z0-9+/=]+)["\']',
            html,
        )
        if encoded_match:
            try:
                decoded = b64decode(encoded_match.group(1)).decode("utf-8", errors="ignore")
                if decoded.startswith("http") and "m3u8" in decoded:
                    return decoded
            except Exception:
                pass

        # Pattern 2: Direct atob('...') with a base64 literal
        atob_match = re.search(r"atob\(['\"]([A-Za-z0-9+/=]+)['\"]\)", html)
        if atob_match:
            try:
                decoded = b64decode(atob_match.group(1)).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

        # Pattern 3: Plain m3u8 URL in source
        m3u8_match = re.search(r"""['"](\s*https?://[^'"]*\.m3u8[^'"]*)['"]""", html)
        if m3u8_match:
            return m3u8_match.group(1).strip()

        return None
