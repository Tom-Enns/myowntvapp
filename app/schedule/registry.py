"""Schedule provider registry."""

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.models import SportEvent

from .base import ScheduleProvider

logger = logging.getLogger(__name__)


@dataclass
class ScheduleResult:
    """Result of fetching events, including any errors encountered."""
    events: list[SportEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provider_id: Optional[str] = None


class ScheduleRegistry:
    """Manages schedule providers with a primary + fallback."""

    def __init__(self):
        self._providers: dict[str, ScheduleProvider] = {}
        self._primary_id: Optional[str] = None

    def register(self, provider: ScheduleProvider) -> None:
        self._providers[provider.provider_id] = provider
        if self._primary_id is None:
            self._primary_id = provider.provider_id
        logger.info(f"Registered schedule provider: {provider.display_name} ({provider.provider_id})")

    def set_primary(self, provider_id: str) -> None:
        if provider_id in self._providers:
            self._primary_id = provider_id

    def get_primary(self) -> Optional[ScheduleProvider]:
        if self._primary_id:
            return self._providers.get(self._primary_id)
        return None

    def list_providers(self) -> list[dict]:
        return [
            {"id": p.provider_id, "name": p.display_name, "primary": p.provider_id == self._primary_id}
            for p in self._providers.values()
        ]

    async def get_events_with_status(self, category: str) -> ScheduleResult:
        """Get events from primary provider, fall back to others on failure.
        Always returns a ScheduleResult with any errors collected."""
        result = ScheduleResult()

        primary = self.get_primary()
        if primary:
            try:
                events = await primary.get_events(category)
                if events:
                    result.events = events
                    result.provider_id = primary.provider_id
                    return result
            except Exception as e:
                logger.warning(f"Primary schedule provider {primary.provider_id} failed: {e}")
                result.errors.append(f"{primary.display_name}: {e}")

        # Try other providers as fallback
        for pid, provider in self._providers.items():
            if pid == self._primary_id:
                continue
            try:
                events = await provider.get_events(category)
                if events:
                    result.events = events
                    result.provider_id = pid
                    return result
            except Exception as e:
                logger.warning(f"Schedule provider {pid} failed: {e}")
                result.errors.append(f"{provider.display_name}: {e}")

        return result

    async def get_events(self, category: str) -> list[SportEvent]:
        """Convenience: just return events (backward compat)."""
        result = await self.get_events_with_status(category)
        return result.events
