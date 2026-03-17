"""Abstract base class for stream sites."""

from abc import ABC, abstractmethod

import aiohttp

from app.resolvers.base import ResolvedEmbed


class StreamSite(ABC):
    """Knows how to navigate a specific streaming site to find the embed/stream.

    Each site declares which domains it handles. The site registry
    matches page URLs against these domains to auto-select the right site.

    Sites sit between backends (which find game pages) and resolvers
    (which extract m3u8 from embed iframes). A site's job is:
      1. Fetch a page on the site
      2. Find the iframe/embed (site-specific HTML structure)
      3. Delegate to the resolver registry for m3u8 extraction
      4. Return ResolvedEmbed with the stream URL and headers
    """

    @property
    @abstractmethod
    def site_id(self) -> str:
        """Unique identifier, e.g. 'thetvapp', 'streameast'."""
        ...

    @property
    @abstractmethod
    def domains(self) -> list[str]:
        """List of domains this site handles, e.g. ['thetvapp.to', 'thetvapp.link'].

        An empty list means this is a fallback/generic site handler.
        """
        ...

    @abstractmethod
    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch a page on this site and extract the stream.

        Args:
            url: The page URL on this site.
            referer: The referring page (e.g. aggregator game page).
            session: An aiohttp session to reuse.

        Returns:
            ResolvedEmbed with m3u8 URL and required headers, or None.
        """
        ...
