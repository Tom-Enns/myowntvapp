"""Prostreams site handler — handles prostreams.su/prostreams.shop pages.

Prostreams uses a multi-layer chain:
  prostreams.su → iframe to prostreams.shop/now/stream-{N}.php
               → iframe to freestyleridesx.lol/premiumtv/prostreams.php?id={N}
               → API call to {M3U8_SERVER}/server_lookup?channel_id={CHANNEL_KEY}
               → m3u8 at {M3U8_SERVER}/proxy/{server_key}/{CHANNEL_KEY}/mono.css

Despite 678KB of obfuscated JS, the key variables (M3U8_SERVER, CHANNEL_KEY)
are plain text in the HTML. The m3u8 URL is constructed by calling a simple
JSON API and building a URL from the response — no JS execution needed.

The .css extension is a disguise — the response is standard HLS (m3u8) with
AES-128 encryption.
"""

import logging
import re
from urllib.parse import urljoin

import aiohttp

from app.resolvers.base import ResolvedEmbed
from app.sites.base import StreamSite

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class ProStreamsSite(StreamSite):
    """Handles stream extraction from ProStreams pages."""

    @property
    def site_id(self) -> str:
        return "prostreams"

    @property
    def domains(self) -> list[str]:
        return [
            "prostreams.su",
            "prostreams.shop",
            "freestyleridesx.lol",
        ]

    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        # Determine which level of the chain we're at
        if "freestyleridesx.lol" in url:
            # Direct embed page — extract from here
            return await self._resolve_embed(url, referer, session)
        elif "prostreams.shop" in url:
            # Intermediate page — find the freestyleridesx iframe
            return await self._resolve_shop_page(url, referer, session)
        else:
            # Top-level prostreams.su page — find the shop iframe
            return await self._resolve_top_page(url, referer, session)

    async def _resolve_top_page(self, url: str, referer: str | None,
                                 session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch prostreams.su and find the prostreams.shop iframe."""
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[prostreams] Failed to fetch {url[:80]}: {e}")
            return None

        # Find the prostreams.shop iframe
        iframe_match = re.search(
            r'<iframe[^>]*src=["\']([^"\']*prostreams\.shop[^"\']*)["\']',
            html, re.IGNORECASE,
        )
        if iframe_match:
            shop_url = iframe_match.group(1)
            if not shop_url.startswith("http"):
                shop_url = urljoin(page_url, shop_url)
            return await self._resolve_shop_page(shop_url, page_url, session)

        logger.debug(f"[prostreams] No shop iframe found on {url[:80]}")
        return None

    async def _resolve_shop_page(self, url: str, referer: str | None,
                                  session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch prostreams.shop and find the freestyleridesx iframe."""
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                page_url = str(resp.url)
        except Exception as e:
            logger.debug(f"[prostreams] Failed to fetch shop page: {e}")
            return None

        # Find the freestyleridesx iframe
        iframe_match = re.search(
            r'<iframe[^>]*src=["\']([^"\']*freestyleridesx[^"\']*)["\']',
            html, re.IGNORECASE,
        )
        if iframe_match:
            embed_url = iframe_match.group(1)
            if not embed_url.startswith("http"):
                embed_url = urljoin(page_url, embed_url)
            return await self._resolve_embed(embed_url, page_url, session)

        logger.debug(f"[prostreams] No embed iframe found on shop page")
        return None

    async def _resolve_embed(self, url: str, referer: str | None,
                              session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch the embed page, extract M3U8_SERVER + CHANNEL_KEY, call the API."""
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[prostreams] Embed returned {resp.status}")
                    return None
                html = await resp.text()
        except Exception as e:
            logger.debug(f"[prostreams] Failed to fetch embed: {e}")
            return None

        # Extract M3U8_SERVER and CHANNEL_KEY
        server_match = re.search(
            r"M3U8_SERVER\s*=\s*['\"]([^'\"]+)['\"]", html
        )
        channel_match = re.search(
            r"CHANNEL_KEY\s*=\s*['\"]([^'\"]+)['\"]", html
        )

        if not server_match or not channel_match:
            logger.debug("[prostreams] Could not find M3U8_SERVER or CHANNEL_KEY")
            return None

        m3u8_server = server_match.group(1)
        channel_key = channel_match.group(1)
        logger.info(f"[prostreams] Server={m3u8_server}, Channel={channel_key}")

        # Call the server_lookup API
        lookup_url = f"https://{m3u8_server}/server_lookup?channel_id={channel_key}"
        try:
            async with session.get(
                lookup_url,
                headers={"User-Agent": _UA, "Referer": url},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"[prostreams] Server lookup returned {resp.status}")
                    return None
                data = await resp.json()
        except Exception as e:
            logger.debug(f"[prostreams] Server lookup failed: {e}")
            return None

        server_key = data.get("server_key", "")
        if not server_key:
            logger.debug("[prostreams] No server_key in lookup response")
            return None

        # Build the m3u8 URL (note: .css extension is a disguise)
        if server_key == "top1/cdn":
            m3u8_url = f"https://{m3u8_server}/proxy/top1/cdn/{channel_key}/mono.css"
        else:
            m3u8_url = f"https://{m3u8_server}/proxy/{server_key}/{channel_key}/mono.css"

        logger.info(f"[prostreams] Resolved stream: {m3u8_url[:80]}")
        return ResolvedEmbed(
            m3u8_url=m3u8_url,
            headers={"Referer": url, "Origin": f"https://{m3u8_server}"},
        )
