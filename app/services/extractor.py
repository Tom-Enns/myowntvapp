import re
from base64 import b64decode
from dataclasses import dataclass, field
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup


@dataclass
class StreamInfo:
    m3u8_url: str
    headers: dict[str, str]
    cookies: list[dict] = field(default_factory=list)


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class StreamExtractor:
    """Extract HLS stream URLs using plain HTTP requests — no browser needed."""

    async def start(self):
        """No-op — kept for API compatibility with the old Playwright extractor."""
        pass

    async def stop(self):
        """No-op."""
        pass

    async def extract(self, url: str, timeout_s: int = 45) -> StreamInfo:
        if url.lower().split("?")[0].endswith(".m3u8"):
            print(f"[extractor] Direct m3u8 URL provided: {url}")
            return StreamInfo(m3u8_url=url, headers={})

        print(f"[extractor] Loading: {url}")
        headers = {"User-Agent": _UA}

        async with aiohttp.ClientSession(headers=headers) as session:
            # Step 1: Fetch the main page
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Page returned {resp.status}")
                page_html = await resp.text()
                page_url = str(resp.url)
                # Capture session cookies for token endpoint
                page_cookies = {k: v.value for k, v in resp.cookies.items()}

            print("[extractor] Page loaded")
            soup = BeautifulSoup(page_html, "html.parser")

            # Check if this is a TV channel page (JWPlayer with token endpoint)
            stream_name_div = soup.find(id="stream_name")
            if stream_name_div and stream_name_div.get("name"):
                return await self._extract_tv_channel(session, page_url, page_cookies, stream_name_div["name"])

            # Otherwise, handle as iframe-based sports stream
            return await self._extract_iframe_stream(session, soup, page_url, page_html)

    async def _extract_tv_channel(self, session: aiohttp.ClientSession,
                                   page_url: str, cookies: dict, stream_name: str) -> StreamInfo:
        """Extract stream URL for TV channels via the /token/ endpoint."""
        print(f"[extractor] TV channel detected: {stream_name}")

        origin = page_url.split("/")[0] + "//" + page_url.split("/")[2]
        token_url = f"{origin}/token/{stream_name}"

        token_headers = {
            "User-Agent": _UA,
            "Referer": page_url,
        }

        async with session.get(token_url, headers=token_headers, cookies=cookies,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Token endpoint returned {resp.status}")
            data = await resp.json()

        m3u8_url = data.get("url")
        if not m3u8_url:
            raise RuntimeError("Token endpoint did not return a stream URL")

        print(f"[extractor] Got authenticated stream URL: {m3u8_url[:120]}...")
        return StreamInfo(m3u8_url=m3u8_url, headers={"Referer": page_url, "Origin": origin})

    async def _extract_iframe_stream(self, session: aiohttp.ClientSession,
                                      soup: BeautifulSoup, page_url: str, page_html: str) -> StreamInfo:
        """Extract stream URL from iframe-based sports event pages."""
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

        print(f"[extractor] Scanning iframe: {iframe_url[:100]}")

        iframe_headers = {
            "User-Agent": _UA,
            "Referer": page_url,
        }
        async with session.get(iframe_url, headers=iframe_headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Iframe returned {resp.status}")
            iframe_html = await resp.text()

        stream_url = self._find_stream_in_html(iframe_html)

        if not stream_url:
            raise RuntimeError("No HLS stream found. The page may not have an active stream right now.")

        origin = iframe_url.split("/")[0] + "//" + iframe_url.split("/")[2]
        stream_headers = {
            "Referer": iframe_url,
            "Origin": origin,
        }

        print(f"[extractor] Found stream URL from JS: {stream_url[:200]}")
        return StreamInfo(m3u8_url=stream_url, headers=stream_headers)

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

        # Strategy 2: source: 'https://...' pattern (Clappr/video player config)
        src_match = re.search(r"source:\s*['\"]?(https?://[^'\"\s,]+)", html)
        if src_match:
            return src_match.group(1)

        # Strategy 3: Direct .m3u8 URL in JS
        m3u8_match = re.search(r"""['"](https?://[^'"]*\.m3u8[^'"]*)['"]""", html)
        if m3u8_match:
            return m3u8_match.group(1)

        # Strategy 4: URL containing 'playlist' and 'load' (common CDN pattern)
        playlist_match = re.search(r"""['"](https?://[^'"]*playlist[^'"]*load[^'"]*)['"]""", html)
        if playlist_match:
            return playlist_match.group(1)

        # Strategy 5: URL containing '/playlist/' (broader match)
        playlist_match2 = re.search(r"""['"](https?://[^'"]*/playlist/[^'"]*)['"]""", html)
        if playlist_match2:
            return playlist_match2.group(1)

        return None
