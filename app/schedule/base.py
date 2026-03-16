"""Abstract base class for schedule providers."""

from abc import ABC, abstractmethod

from app.models import SportEvent


class ScheduleProvider(ABC):
    """Interface for fetching upcoming sport schedules."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @abstractmethod
    async def get_events(self, category: str) -> list[SportEvent]:
        """Return upcoming events for a sport category."""
        ...

    @abstractmethod
    def supported_categories(self) -> list[str]:
        ...
