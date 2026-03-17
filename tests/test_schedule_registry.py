"""Tests for the schedule registry."""

import pytest

from app.models import SportEvent
from app.schedule.base import ScheduleProvider
from app.schedule.registry import ScheduleRegistry, ScheduleResult


class MockScheduleProvider(ScheduleProvider):
    """Mock provider. categories controls supported_categories (default: many = general)."""
    def __init__(self, pid: str, name: str, events=None, error=None,
                 categories=None):
        self._id = pid
        self._name = name
        self._events = events or []
        self._error = error
        self._categories = categories or ["nba", "nhl", "mlb", "nfl", "soccer"]

    @property
    def provider_id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._name

    def supported_categories(self) -> list[str]:
        return self._categories

    async def get_events(self, category: str) -> list[SportEvent]:
        if self._error:
            raise self._error
        return self._events


def _make_events(n=2, category="nba"):
    return [
        SportEvent(event_id=f"test:{i}", title=f"Game {i}", category=category)
        for i in range(n)
    ]


class TestScheduleRegistry:
    def test_register_and_list(self):
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("a", "Provider A"))
        reg.register(MockScheduleProvider("b", "Provider B"))

        providers = reg.list_providers()
        assert len(providers) == 2
        # First registered becomes primary
        assert providers[0]["primary"] is True

    def test_set_primary(self):
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("a", "A"))
        reg.register(MockScheduleProvider("b", "B"))
        reg.set_primary("b")

        primary = reg.get_primary()
        assert primary.provider_id == "b"

    @pytest.mark.asyncio
    async def test_get_events_from_primary(self):
        events = _make_events(3)
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("primary", "Primary", events=events))
        reg.register(MockScheduleProvider("fallback", "Fallback", events=_make_events(1)))

        result = await reg.get_events_with_status("nba")
        assert len(result.events) == 3
        assert result.provider_id == "primary"
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self):
        fallback_events = _make_events(2)
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("primary", "Primary", error=PermissionError("403 blocked")))
        reg.register(MockScheduleProvider("fallback", "Fallback", events=fallback_events))

        result = await reg.get_events_with_status("nba")
        assert len(result.events) == 2
        assert result.provider_id == "fallback"
        # The primary's error should be captured
        assert len(result.errors) == 1
        assert "403 blocked" in result.errors[0]

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("a", "A", error=PermissionError("blocked")))
        reg.register(MockScheduleProvider("b", "B", error=ConnectionError("unreachable")))

        result = await reg.get_events_with_status("nba")
        assert result.events == []
        assert len(result.errors) == 2

    @pytest.mark.asyncio
    async def test_primary_empty_falls_through(self):
        """If primary returns empty list (no events), try fallback."""
        fallback_events = _make_events(2)
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("primary", "Primary", events=[]))
        reg.register(MockScheduleProvider("fallback", "Fallback", events=fallback_events))

        result = await reg.get_events_with_status("nba")
        assert len(result.events) == 2
        assert result.provider_id == "fallback"

    @pytest.mark.asyncio
    async def test_backward_compat_get_events(self):
        events = _make_events(2)
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("a", "A", events=events))

        result = await reg.get_events("nba")
        assert len(result) == 2
        assert isinstance(result[0], SportEvent)


class TestSpecializedProviderPriority:
    """Test that specialized providers (few categories) are tried before general ones."""

    @pytest.mark.asyncio
    async def test_specialized_tried_before_general(self):
        """NHL-specialized provider should be tried before general providers for NHL."""
        general_events = _make_events(2, category="nhl")
        specialized_events = [
            SportEvent(event_id="nhl:1", title="Official NHL Game", category="nhl")
        ]

        reg = ScheduleRegistry()
        # Register general first (would normally be primary)
        reg.register(MockScheduleProvider(
            "general", "General", events=general_events,
            categories=["nba", "nhl", "mlb", "nfl", "soccer"],
        ))
        # Register specialized after
        reg.register(MockScheduleProvider(
            "nhl_official", "NHL Official", events=specialized_events,
            categories=["nhl"],
        ))

        result = await reg.get_events_with_status("nhl")
        # Specialized provider should win even though general was registered first
        assert result.provider_id == "nhl_official"
        assert len(result.events) == 1
        assert result.events[0].event_id == "nhl:1"

    @pytest.mark.asyncio
    async def test_general_still_used_for_other_categories(self):
        """General provider should still be used for categories the specialized one doesn't support."""
        general_events = _make_events(3, category="nba")

        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider(
            "general", "General", events=general_events,
            categories=["nba", "nhl", "mlb", "nfl", "soccer"],
        ))
        reg.register(MockScheduleProvider(
            "nhl_official", "NHL Official",
            events=[SportEvent(event_id="nhl:1", title="Game", category="nhl")],
            categories=["nhl"],
        ))

        result = await reg.get_events_with_status("nba")
        # NHL provider doesn't support NBA, so general wins
        assert result.provider_id == "general"
        assert len(result.events) == 3

    @pytest.mark.asyncio
    async def test_specialized_fails_falls_back_to_general(self):
        """If specialized provider fails, general should still work."""
        general_events = _make_events(2, category="nhl")

        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider(
            "general", "General", events=general_events,
            categories=["nba", "nhl", "mlb", "nfl", "soccer"],
        ))
        reg.register(MockScheduleProvider(
            "nhl_official", "NHL Official",
            error=RuntimeError("NHL API down"),
            categories=["nhl"],
        ))

        result = await reg.get_events_with_status("nhl")
        assert result.provider_id == "general"
        assert len(result.events) == 2
        # Error from specialized provider should be captured
        assert len(result.errors) == 1
        assert "NHL API down" in result.errors[0]

    @pytest.mark.asyncio
    async def test_primary_preferred_among_general(self):
        """Among general providers, primary should be tried first."""
        primary_events = _make_events(3, category="nba")
        other_events = _make_events(1, category="nba")

        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider(
            "other", "Other", events=other_events,
            categories=["nba", "nhl", "mlb", "nfl"],
        ))
        reg.register(MockScheduleProvider(
            "primary", "Primary", events=primary_events,
            categories=["nba", "nhl", "mlb", "nfl", "soccer"],
        ))
        reg.set_primary("primary")

        result = await reg.get_events_with_status("nba")
        assert result.provider_id == "primary"
        assert len(result.events) == 3

    def test_list_providers_includes_categories(self):
        reg = ScheduleRegistry()
        reg.register(MockScheduleProvider("a", "A", categories=["nhl"]))
        reg.register(MockScheduleProvider("b", "B", categories=["nba", "nhl", "mlb"]))

        providers = reg.list_providers()
        assert providers[0]["categories"] == ["nhl"]
        assert providers[1]["categories"] == ["nba", "nhl", "mlb"]
