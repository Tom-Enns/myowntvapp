"""Tests for error handling — ensuring users see clear error messages
instead of gray screens when backends fail.

Covers the exact scenario: user is blocked (403) from thetvapp.to.
"""

import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses

from app.backends.thetvapp import TheTVAppBackend
from app.backends.registry import BackendRegistry
from app.schedule.thetvapp_schedule import TheTVAppSchedule
from app.schedule.registry import ScheduleRegistry
from app.models import SportEvent


@pytest.fixture
def backend():
    return TheTVAppBackend()


@pytest.fixture
def schedule_provider(mock_logo_service):
    return TheTVAppSchedule(mock_logo_service)


# ---------------------------------------------------------------
# Backend: stream resolution errors (tested through resolve_stream)
# ---------------------------------------------------------------

class TestBackendErrorHandling:
    """Test that backend errors produce clear, user-facing messages."""

    @pytest.mark.asyncio
    async def test_403_returns_none(self, backend):
        """When the site returns 403, resolve_stream returns None."""
        event = SportEvent(
            event_id="thetvapp:test",
            title="Test Game",
            category="nba",
        )
        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", status=403)

            stream = await backend.resolve_stream(event)
            assert stream is None

    @pytest.mark.asyncio
    async def test_500_returns_none(self, backend):
        event = SportEvent(
            event_id="thetvapp:test",
            title="Test Game",
            category="nba",
        )
        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", status=500)

            stream = await backend.resolve_stream(event)
            assert stream is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self, backend):
        event = SportEvent(
            event_id="thetvapp:test",
            title="Test Game",
            category="nba",
        )
        with aioresponses() as m:
            m.get(
                "https://thetvapp.to/event/test/",
                exception=aiohttp.ClientConnectionError("Connection refused"),
            )

            stream = await backend.resolve_stream(event)
            assert stream is None


# ---------------------------------------------------------------
# Schedule provider: event listing errors
# ---------------------------------------------------------------

class TestScheduleErrorHandling:
    """Test that schedule provider errors are raised, not swallowed."""

    @pytest.mark.asyncio
    async def test_403_on_listing(self, schedule_provider):
        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", status=403)

            with pytest.raises(PermissionError, match="403 Forbidden"):
                await schedule_provider.get_events("nba")

    @pytest.mark.asyncio
    async def test_500_on_listing(self, schedule_provider):
        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", status=500)

            with pytest.raises(RuntimeError, match="HTTP 500"):
                await schedule_provider.get_events("nba")

    @pytest.mark.asyncio
    async def test_connection_error_on_listing(self, schedule_provider):
        with aioresponses() as m:
            m.get(
                "https://thetvapp.to/nba",
                exception=aiohttp.ClientConnectionError("Name or service not known"),
            )

            with pytest.raises((ConnectionError, aiohttp.ClientConnectionError), match="[Cc]onnect"):
                await schedule_provider.get_events("nba")

    @pytest.mark.asyncio
    async def test_timeout_on_listing(self, schedule_provider):
        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", exception=asyncio.TimeoutError())

            with pytest.raises(TimeoutError, match="did not respond"):
                await schedule_provider.get_events("nba")


# ---------------------------------------------------------------
# Registry: errors propagated to API layer
# ---------------------------------------------------------------

class TestRegistryErrorPropagation:
    """Test that registries collect and surface errors to the API."""

    @pytest.mark.asyncio
    async def test_schedule_registry_captures_403(self, schedule_provider):
        reg = ScheduleRegistry()
        reg.register(schedule_provider)

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", status=403)
            result = await reg.get_events_with_status("nba")

        assert result.events == []
        assert len(result.errors) == 1
        assert "403" in result.errors[0]
        assert "blocked" in result.errors[0].lower()

    @pytest.mark.asyncio
    async def test_backend_registry_captures_all_failures(self):
        """When all backends fail, resolve_best returns per-backend errors."""
        reg = BackendRegistry()
        reg.register(TheTVAppBackend())

        event = SportEvent(
            event_id="thetvapp:fake-event",
            title="Fake Game",
            category="nba",
        )

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/fake-event/", status=403)

            stream, attempts = await reg.resolve_best(event)

        assert stream is None
        assert len(attempts) == 1
        assert not attempts[0].success
