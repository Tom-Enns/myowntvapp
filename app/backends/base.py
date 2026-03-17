"""Abstract base class for stream backends."""

from abc import ABC, abstractmethod

from app.models import SportEvent, ResolvedStream, StreamLink


class StreamBackend(ABC):
    """Interface every stream backend must implement."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Unique identifier, e.g. 'thetvapp'."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'TheTVApp.to'."""
        ...

    @abstractmethod
    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        """Given a sport event, attempt to find a working stream.
        Return None if this backend can't serve it."""
        ...

    async def resolve_streams(self, event: SportEvent) -> list[ResolvedStream]:
        """Return ALL available streams for an event.

        Aggregator backends override this to return multiple streams.
        Default implementation wraps resolve_stream() in a list.
        """
        stream = await self.resolve_stream(event)
        return [stream] if stream else []

    async def discover_links(self, event: SportEvent) -> list[StreamLink]:
        """Discover stream page URLs for an event without resolving them.

        This is the first phase of the two-phase resolution flow:
          1. discover_links() — all backends find URLs in parallel
          2. Orchestrator deduplicates URLs, resolves via site registry

        Direct backends return a single link (the game page URL).
        Aggregators return many links (external stream site URLs).
        """
        return []

    async def health_check(self) -> bool:
        """Optional: verify backend is reachable."""
        return True
