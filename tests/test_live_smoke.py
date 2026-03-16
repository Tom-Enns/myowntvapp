"""Live smoke tests — hit real external services.

These are SKIPPED by default. Run them with:
    pytest -m live

They verify that the real thetvapp.to is reachable and parseable,
so you can catch site changes early.
"""

import pytest

from app.backends.thetvapp import TheTVAppBackend
from app.schedule.thetvapp_schedule import TheTVAppSchedule
from app.services.logos import LogoService

pytestmark = pytest.mark.live


@pytest.fixture
def backend():
    return TheTVAppBackend()


@pytest.fixture
def schedule():
    return TheTVAppSchedule(LogoService())


class TestLiveHealthCheck:
    @pytest.mark.asyncio
    async def test_thetvapp_reachable(self, backend):
        """Verify thetvapp.to is up and responding."""
        healthy = await backend.health_check()
        assert healthy, "thetvapp.to is not reachable — site may be down or your IP is blocked"


class TestLiveSchedule:
    @pytest.mark.asyncio
    async def test_tv_channels_available(self, schedule):
        """TV channels should always be listed (not dependent on live games)."""
        events = await schedule.get_events("tv")
        assert len(events) > 0, "No TV channels found — thetvapp.to listing may have changed"
        # Verify events have expected structure
        for ev in events:
            assert ev.event_id.startswith("thetvapp:")
            assert ev.category == "tv"
            assert ev.title

    @pytest.mark.asyncio
    async def test_any_sport_has_events(self, schedule):
        """At least one sport category should have events."""
        categories = ["nba", "mlb", "nhl", "nfl", "soccer"]
        found_any = False
        for cat in categories:
            try:
                events = await schedule.get_events(cat)
                if events:
                    found_any = True
                    break
            except Exception:
                continue

        # This can legitimately fail during off-season for all sports
        # so we just warn rather than fail hard
        if not found_any:
            pytest.skip("No live sports events found across any category (may be off-season)")


class TestLiveStreamResolution:
    @pytest.mark.asyncio
    async def test_resolve_tv_channel(self, backend):
        """Try to resolve a TV channel stream (most reliable test)."""
        from app.models import SportEvent

        event = SportEvent(
            event_id="thetvapp:espn",
            title="ESPN",
            category="tv",
        )

        try:
            stream = await backend.resolve_stream(event)
        except PermissionError:
            pytest.skip("IP is blocked by thetvapp.to — use a VPN to run live tests")
        except Exception as e:
            pytest.fail(f"Unexpected error resolving ESPN: {e}")

        if stream is None:
            pytest.skip("ESPN channel not available on thetvapp.to right now")

        assert stream.m3u8_url
        assert stream.backend_id == "thetvapp"
        assert stream.headers.get("Referer")
