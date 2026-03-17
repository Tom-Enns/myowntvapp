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
    """Manages schedule providers with a primary + fallback.

    For a given category, providers are tried in this order:
    1. Category-specialized providers (those supporting only a few categories)
       — e.g. NHL Official API for 'nhl'
    2. The primary provider (general-purpose, like thetvapp)
    3. Other general providers as fallback
    """

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
            {
                "id": p.provider_id,
                "name": p.display_name,
                "primary": p.provider_id == self._primary_id,
                "categories": p.supported_categories(),
            }
            for p in self._providers.values()
        ]

    def _get_ordered_providers(self, category: str) -> list[ScheduleProvider]:
        """Return providers ordered: specialized first, then primary, then rest."""
        specialized = []
        general = []

        for pid, provider in self._providers.items():
            cats = provider.supported_categories()
            supports = category.lower() in [c.lower() for c in cats]
            if not supports:
                continue
            # "Specialized" = supports fewer categories than general providers
            if len(cats) <= 4:
                specialized.append(provider)
            else:
                general.append(provider)

        # Within general, put primary first
        general.sort(key=lambda p: 0 if p.provider_id == self._primary_id else 1)

        return specialized + general

    async def get_events_with_status(self, category: str) -> ScheduleResult:
        """Get events, trying specialized providers first, then primary, then fallbacks.
        Always returns a ScheduleResult with any errors collected."""
        result = ScheduleResult()

        for provider in self._get_ordered_providers(category):
            try:
                events = await provider.get_events(category)
                if events:
                    result.events = events
                    result.provider_id = provider.provider_id
                    return result
            except Exception as e:
                logger.warning(f"Schedule provider {provider.provider_id} failed: {e}")
                result.errors.append(f"{provider.display_name}: {e}")

        return result

    async def get_events(self, category: str) -> list[SportEvent]:
        """Convenience: just return events (backward compat)."""
        result = await self.get_events_with_status(category)
        return result.events
