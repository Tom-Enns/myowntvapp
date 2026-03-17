"""Abstract base class for stream resolvers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import aiohttp


@dataclass
class ResolvedEmbed:
    """Result of resolving a single embed/iframe URL."""
    m3u8_url: str
    headers: dict[str, str]
    label: str | None = None  # Source-provided quality label ("HD", "720p", etc.)


class StreamResolver(ABC):
    """Knows how to extract an m3u8 stream from a specific embed host.

    Each resolver declares which domains it handles. The resolver registry
    matches embed URLs against these domains to auto-select the right resolver.
    """

    @property
    @abstractmethod
    def resolver_id(self) -> str:
        """Unique identifier, e.g. 'generic', 'filemoon'."""
        ...

    @property
    @abstractmethod
    def domains(self) -> list[str]:
        """List of domains this resolver handles, e.g. ['gooz.aapmains.net'].

        An empty list means this is a fallback/generic resolver.
        """
        ...

    @abstractmethod
    async def resolve(self, url: str, referer: str,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Fetch the embed page and extract the m3u8 URL.

        Args:
            url: The embed/iframe URL to resolve.
            referer: The page that contained the iframe (for Referer header).
            session: An aiohttp session to reuse.

        Returns:
            ResolvedEmbed with m3u8 URL and required headers, or None.
        """
        ...
