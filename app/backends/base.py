"""Abstract base class for stream backends."""

from abc import ABC, abstractmethod

from app.models import SportEvent, ResolvedStream


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

    async def health_check(self) -> bool:
        """Optional: verify backend is reachable."""
        return True
