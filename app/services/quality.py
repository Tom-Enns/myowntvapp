"""Stream quality detection — parses HLS master playlists for variant info."""

import logging

import aiohttp
import m3u8

from app.models import StreamQuality

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def parse_stream_qualities(m3u8_url: str, headers: dict[str, str]) -> list[StreamQuality]:
    """Fetch a master playlist and extract quality info from variants.

    Returns sorted list (highest quality first).
    If the URL points to a media playlist (not master), returns an empty list.
    """
    try:
        fetch_headers = {"User-Agent": _UA}
        fetch_headers.update(headers)

        async with aiohttp.ClientSession() as session:
            async with session.get(m3u8_url, headers=fetch_headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()

        parsed = m3u8.loads(text)

        if not parsed.playlists:
            # Media playlist, not master — no variant info available
            return []

        qualities = []
        for playlist in parsed.playlists:
            info = playlist.stream_info
            if not info:
                continue

            width = None
            height = None
            resolution_str = None

            if info.resolution:
                width, height = info.resolution
                resolution_str = f"{height}p"

            qualities.append(StreamQuality(
                resolution=resolution_str,
                width=width,
                height=height,
                bandwidth=info.bandwidth,
                codecs=info.codecs,
                frame_rate=info.frame_rate,
            ))

        # Sort by bandwidth descending (highest quality first)
        qualities.sort(key=lambda q: q.bandwidth or 0, reverse=True)
        return qualities

    except Exception as e:
        logger.warning(f"Failed to parse qualities from {m3u8_url[:80]}: {e}")
        return []
