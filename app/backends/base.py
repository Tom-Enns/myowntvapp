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

    async def health_check(self) -> bool:
        """Optional: verify backend is reachable."""
        return True
