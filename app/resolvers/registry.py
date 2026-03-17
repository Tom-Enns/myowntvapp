"""Resolver registry — auto-selects the right stream resolver based on embed URL domain."""

import logging
from urllib.parse import urlparse

import aiohttp

from app.resolvers.base import StreamResolver, ResolvedEmbed
from app.resolvers.generic import GenericResolver

logger = logging.getLogger(__name__)


class ResolverRegistry:
    """Maintains a registry of stream resolvers keyed by domain.

    When given an embed URL, it looks up the domain and dispatches
    to the matching resolver. Falls back to the generic resolver
    if no domain-specific resolver is registered.
    """

    def __init__(self):
        self._resolvers: dict[str, StreamResolver] = {}
        self._fallback = GenericResolver()

    def register(self, resolver: StreamResolver) -> None:
        """Register a resolver for its declared domains."""
        for domain in resolver.domains:
            self._resolvers[domain.lower()] = resolver
            logger.info(f"Registered resolver '{resolver.resolver_id}' for domain: {domain}")
        if not resolver.domains:
            logger.info(f"Registered fallback resolver: {resolver.resolver_id}")

    def get_resolver(self, url: str) -> StreamResolver:
        """Auto-detect the right resolver based on URL hostname."""
        try:
            hostname = urlparse(url).hostname or ""
        except Exception:
            return self._fallback

        # Exact match first
        if hostname.lower() in self._resolvers:
            return self._resolvers[hostname.lower()]

        # Try matching parent domains (e.g., "sub.example.com" matches "example.com")
        parts = hostname.lower().split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in self._resolvers:
                return self._resolvers[parent]

        return self._fallback

    async def resolve(self, url: str, referer: str,
                      session: aiohttp.ClientSession) -> ResolvedEmbed | None:
        """Resolve an embed URL by auto-detecting and using the right resolver."""
        resolver = self.get_resolver(url)
        logger.debug(f"Using resolver '{resolver.resolver_id}' for {url[:60]}")
        return await resolver.resolve(url, referer, session)

    def list_resolvers(self) -> list[dict]:
        """Return info about registered resolvers."""
        seen = set()
        result = []
        for resolver in self._resolvers.values():
            if resolver.resolver_id not in seen:
                seen.add(resolver.resolver_id)
                result.append({
                    "id": resolver.resolver_id,
                    "domains": resolver.domains,
                })
        result.append({"id": self._fallback.resolver_id, "domains": ["(fallback)"]})
        return result


# Shared singleton — backends import and use this
_registry: ResolverRegistry | None = None


def get_resolver_registry() -> ResolverRegistry:
    """Get or create the shared resolver registry with all known resolvers."""
    global _registry
    if _registry is None:
        _registry = ResolverRegistry()
        # Register the generic fallback (handles atob, source, m3u8, playlist patterns)
        _registry.register(GenericResolver())
        # Future: register domain-specific resolvers here
        # from app.resolvers.filemoon import FilemoonResolver
        # _registry.register(FilemoonResolver())
    return _registry
