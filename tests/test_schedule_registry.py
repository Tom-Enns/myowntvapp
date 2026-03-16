"""Tests for the schedule registry."""

import pytest

from app.models import SportEvent
from app.schedule.base import ScheduleProvider
from app.schedule.registry import ScheduleRegistry, ScheduleResult


class MockScheduleProvider(ScheduleProvider):
    def __init__(self, pid: str, name: str, events=None, error=None):
        self._id = pid
        self._name = name
        self._events = events or []
        self._error = error

    @property
    def provider_id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._name

    def supported_categories(self) -> list[str]:
        return ["nba"]

    async def get_events(self, category: str) -> list[SportEvent]:
        if self._error:
            raise self._error
        return self._events


def _make_events(n=2):
    return [
        SportEvent(event_id=f"test:{i}", title=f"Game {i}", category="nba")
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
        assert "blocked" in result.errors[0]
        assert "unreachable" in result.errors[1]

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
