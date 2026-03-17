"""Site registry — auto-selects the right site handler based on page URL domain."""

import logging
from urllib.parse import urlparse

import aiohttp

from app.resolvers.base import ResolvedEmbed
from app.sites.base import StreamSite
from app.sites.generic import GenericSite

logger = logging.getLogger(__name__)


class SiteRegistry:
    """Maintains a registry of site handlers keyed by domain.

    When given a page URL, it looks up the domain and dispatches
    to the matching site handler. Falls back to the generic site
    if no domain-specific handler is registered.
    """

    def __init__(self):
        self._sites: dict[str, StreamSite] = {}
        self._fallback = GenericSite()

    def register(self, site: StreamSite) -> None:
        """Register a site handler for its declared domains."""
        for domain in site.domains:
            self._sites[domain.lower()] = site
            logger.info(f"Registered site '{site.site_id}' for domain: {domain}")
        if not site.domains:
            logger.info(f"Registered fallback site: {site.site_id}")

    def get_site(self, url: str) -> StreamSite:
        """Auto-detect the right site handler based on URL hostname."""
        try:
            hostname = urlparse(url).hostname or ""
        except Exception:
            return self._fallback

        # Exact match first
        if hostname.lower() in self._sites:
            return self._sites[hostname.lower()]

        # Try matching parent domains (e.g., "www.thetvapp.to" matches "thetvapp.to")
        parts = hostname.lower().split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in self._sites:
                return self._sites[parent]

        return self._fallback

    async def resolve(self, url: str, referer: str | None,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Resolve a page URL by auto-detecting and using the right site handler."""
        site = self.get_site(url)
        logger.debug(f"Using site '{site.site_id}' for {url[:60]}")
        return await site.resolve(url, referer, session)

    def list_sites(self) -> list[dict]:
        """Return info about registered sites."""
        seen = set()
        result = []
        for site in self._sites.values():
            if site.site_id not in seen:
                seen.add(site.site_id)
                result.append({
                    "id": site.site_id,
                    "domains": site.domains,
                })
        result.append({"id": self._fallback.site_id, "domains": ["(fallback)"]})
        return result


# Shared singleton
_registry: SiteRegistry | None = None


def get_site_registry() -> SiteRegistry:
    """Get or create the shared site registry with all known site handlers."""
    global _registry
    if _registry is None:
        _registry = SiteRegistry()
        # Register the generic fallback
        _registry.register(GenericSite())
        # Register site-specific handlers
        from app.sites.thetvapp import TheTVAppSite
        _registry.register(TheTVAppSite())
        from app.sites.streameast import StreamEastSite
        _registry.register(StreamEastSite())
        from app.sites.sportsurge import SportsurgeSite
        _registry.register(SportsurgeSite())
        from app.sites.streambtw import StreamBTWSite
        _registry.register(StreamBTWSite())
        from app.sites.topstreams import TopStreamsSite
        _registry.register(TopStreamsSite())
        from app.sites.nflwebhunter import NFLWebHunterSite
        _registry.register(NFLWebHunterSite())
        from app.sites.totalsportek import TotalSportekSite
        _registry.register(TotalSportekSite())
    return _registry
