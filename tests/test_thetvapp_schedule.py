"""Tests for the TheTVApp schedule provider with mocked HTTP."""

import pytest
from aioresponses import aioresponses

from app.schedule.thetvapp_schedule import TheTVAppSchedule
from tests.conftest import load_fixture


@pytest.fixture
def provider(mock_logo_service):
    return TheTVAppSchedule(mock_logo_service)


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_parses_nba_listing(self, provider):
        html = load_fixture("thetvapp_nba_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=html)
            events = await provider.get_events("nba")

        assert len(events) == 3
        # Check first event
        assert events[0].event_id == "thetvapp:celtics-at-lakers-20260316"
        assert events[0].category == "nba"
        assert events[0].home_team == "Los Angeles Lakers"
        assert events[0].away_team == "Boston Celtics"
        # Logos fetched
        assert events[0].home_logo is not None
        assert events[0].away_logo is not None

    @pytest.mark.asyncio
    async def test_parses_tv_listing(self, provider):
        html = load_fixture("thetvapp_tv_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/tv", body=html)
            events = await provider.get_events("tv")

        # Should pick up ESPN and Fox Sports 1, but skip the "/tv" link
        assert len(events) == 2
        assert events[0].event_id == "thetvapp:espn"
        assert events[1].event_id == "thetvapp:fox-sports-1"

    @pytest.mark.asyncio
    async def test_parses_vs_format(self, provider):
        html = load_fixture("thetvapp_nba_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=html)
            events = await provider.get_events("nba")

        # Third event uses "vs" format
        vs_event = events[2]
        assert vs_event.home_team == "New York Knicks"
        assert vs_event.away_team == "Miami Heat"

    @pytest.mark.asyncio
    async def test_empty_listing(self, provider):
        html = "<html><body><div class='list-group'></div></body></html>"

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=html)
            events = await provider.get_events("nba")

        assert events == []


class TestSupportedCategories:
    def test_all_categories(self, provider):
        cats = provider.supported_categories()
        assert "nba" in cats
        assert "nfl" in cats
        assert "tv" in cats


class TestGetEventUrl:
    def test_event_url(self, provider, nba_event):
        url = provider.get_event_url(nba_event)
        assert url == "https://thetvapp.to/event/celtics-at-lakers-20260316/"

    def test_tv_url(self, provider, tv_event):
        url = provider.get_event_url(tv_event)
        assert url == "https://thetvapp.to/tv/espn/"

    def test_non_thetvapp_event(self, provider, sportsdb_event):
        url = provider.get_event_url(sportsdb_event)
        assert url is None
