"""TopStreams site handler — handles topstreams.info stream pages.

TopStreams serves m3u8 URLs as plain JavaScript variables in the HTML:
  var globalurl = 'https://lpnba.akamaized.net/live-pz/.../index.m3u8?...';

The player is Shaka Player with DRM clearKeys. We extract the globalurl
variable directly via regex — no JS execution needed.

URL pattern: https://topstreams.info/{sport}/{team}
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


class TopStreamsSite(StreamSite):
    """Handles stream extraction from TopStreams (topstreams.info) pages."""

    @property
    def site_id(self) -> str:
        return "topstreams"

    @property
    def domains(self) -> list[str]:
        return [
            "topstreams.info",
            "topstreamshd.top",
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
                    logger.debug(f"[topstreams] Page returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[topstreams] Failed to fetch {url[:80]}: {e}")
            return None

        m3u8_url = self._extract_stream_url(html)
        if not m3u8_url:
            logger.debug(f"[topstreams] No stream URL found on {url[:80]}")
            return None

        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": page_url, "Origin": "https://topstreams.info"},
        )

    def _extract_stream_url(self, html: str) -> str | None:
        """Extract m3u8 URL from the globalurl JavaScript variable.

        The page contains:
          var globalurl = 'https://...akamaized.net/.../index.m3u8?...';

        We extract it directly with regex.
        """
        # Pattern 1: var globalurl = '...'
        global_match = re.search(
            r"var\s+globalurl\s*=\s*'([^']+)'",
            html,
        )
        if global_match:
            url = global_match.group(1)
            if url.startswith("http"):
                return url

        # Pattern 2: var globalurl = "..."
        global_match2 = re.search(
            r'var\s+globalurl\s*=\s*"([^"]+)"',
            html,
        )
        if global_match2:
            url = global_match2.group(1)
            if url.startswith("http"):
                return url

        # Fallback: any m3u8 URL in a JS variable assignment
        m3u8_match = re.search(
            r"""var\s+\w+\s*=\s*['"](\s*https?://[^'"]*\.m3u8[^'"]*)['"]""",
            html,
        )
        if m3u8_match:
            return m3u8_match.group(1).strip()

        return None
