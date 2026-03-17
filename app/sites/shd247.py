"""SHD247 site handler — handles shd247.live stream pages.

SHD247 (streamhd247) pages contain a relative iframe to a source page
(e.g., source022.html) which itself has an iframe to increasecattle.net.
The increasecattle.net embed page has the m3u8 URL as a plain Clappr
source — no obfuscation needed.

Chain: shd247.live/{page}.html
       → iframe to shd247.live/source{NNN}.html
       → iframe to increasecattle.net/embed/{id}
       → Clappr player with m3u8 URL in HTML
"""

import logging
import re
from urllib.parse import urljoin

import aiohttp

from app.resolvers.base import ResolvedEmbed
from app.resolvers.generic import find_stream_in_html
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class SHD247Site(StreamSite):
    """Handles stream extraction from SHD247 (shd247.live) pages."""

    @property
    def site_id(self) -> str:
        return "shd247"

    @property
    def domains(self) -> list[str]:
        return ["shd247.live"]

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
                    logger.debug(f"[shd247] Page returned {resp.status}: {url[:80]}")
                    return None
                html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[shd247] Failed to fetch {url[:80]}: {e}")
            return None

        # Step 1: Find the source iframe (relative or absolute)
        embed_url = await self._follow_iframe_chain(html, page_url, session)
        if not embed_url:
            logger.debug(f"[shd247] No embed URL found on {url[:80]}")
            return None

        # Step 2: Fetch the final embed page and extract m3u8
        return await self._extract_from_embed(embed_url, page_url, session)

    async def _follow_iframe_chain(self, html: str, base_url: str,
                                    session: aiohttp.ClientSession) -> str | None:
        """Follow the iframe chain to find the final embed URL.

        shd247 pages have a relative iframe (source022.html) which contains
        the real embed iframe (increasecattle.net/embed/{id}).
        """
        # Find the first non-ad iframe
        for match in re.finditer(
            r'<iframe[^>]*src=["\']([^"\']+)["\']', html, re.IGNORECASE
        ):
            src = match.group(1)
            # Skip ad/tracking iframes
            if any(skip in src.lower() for skip in (
                "google", "histats", "facebook", "crwdcntrl", "adex",
            )):
                continue

            # Resolve relative URLs
            iframe_url = urljoin(base_url, src)

            # If it's an external embed (increasecattle, etc.), return it directly
            if "increasecattle" in iframe_url or "embed" in iframe_url:
                return iframe_url

            # If it's a relative source page, fetch it and find the real embed
            if "source" in src.lower() or not src.startswith("http"):
                try:
                    async with session.get(
                        iframe_url,
                        headers={"User-Agent": _UA, "Referer": base_url},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            continue
                        source_html = await resp.text()
                except Exception:
                    continue

                # Find the embed iframe in the source page
                for inner_match in re.finditer(
                    r'<iframe[^>]*src=["\']([^"\']+)["\']',
                    source_html,
                    re.IGNORECASE,
                ):
                    inner_src = inner_match.group(1)
                    if any(skip in inner_src.lower() for skip in (
                        "google", "histats", "facebook",
                    )):
                        continue
                    inner_url = urljoin(iframe_url, inner_src)
                    if inner_url.startswith("http"):
                        return inner_url

        return None

    async def _extract_from_embed(self, embed_url: str, referer: str,
                                   session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch the embed page and extract the m3u8 URL."""
        try:
            async with session.get(
                embed_url,
                headers={"User-Agent": _UA, "Referer": referer},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[shd247] Embed returned {resp.status}: {embed_url[:80]}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.debug(f"[shd247] Failed to fetch embed: {e}")
            return None

        # Use the shared stream finder (catches plain m3u8 URLs, atob, etc.)
        m3u8_url = find_stream_in_html(html)
        if not m3u8_url:
            logger.debug(f"[shd247] No m3u8 found in embed page")
            return None

        origin = embed_url.split("/")[0] + "//" + embed_url.split("/")[2]
        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": embed_url, "Origin": origin},
        )
