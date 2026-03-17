"""Tests for the NHL Official API schedule provider."""

import json

import pytest
from aioresponses import aioresponses

from app.schedule.nhl_schedule import NHLSchedule, NHL_API_BASE
from tests.conftest import load_fixture


@pytest.fixture
def provider():
    return NHLSchedule()


@pytest.fixture
def nhl_response():
    return json.loads(load_fixture("nhl_schedule_response.json"))


class TestNHLScheduleProperties:
    def test_provider_id(self, provider):
        assert provider.provider_id == "nhl"

    def test_display_name(self, provider):
        assert provider.display_name == "NHL Official"

    def test_supported_categories(self, provider):
        assert provider.supported_categories() == ["nhl"]


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_returns_all_games_across_days(self, provider, nhl_response):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload=nhl_response)
            events = await provider.get_events("nhl")

        # 3 games on day 1, 1 game on day 2 = 4 total
        assert len(events) == 4

    @pytest.mark.asyncio
    async def test_future_game_parsing(self, provider, nhl_response):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload=nhl_response)
            events = await provider.get_events("nhl")

        # First game: Boston @ Toronto, future
        bos_tor = events[0]
        assert bos_tor.event_id == "nhl:2025020900"
        assert bos_tor.category == "nhl"
        assert bos_tor.home_team == "Toronto Maple Leafs"
        assert bos_tor.away_team == "Boston Bruins"
        assert "Boston Bruins @ Toronto Maple Leafs" in bos_tor.title
        # Future games should have formatted date/time in title
        assert "\n" in bos_tor.title
        assert bos_tor.start_time is not None

    @pytest.mark.asyncio
    async def test_official_logos(self, provider, nhl_response):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload=nhl_response)
            events = await provider.get_events("nhl")

        bos_tor = events[0]
        assert bos_tor.home_logo == "https://assets.nhle.com/logos/nhl/svg/TOR_light.svg"
        assert bos_tor.away_logo == "https://assets.nhle.com/logos/nhl/svg/BOS_light.svg"

    @pytest.mark.asyncio
    async def test_live_game_parsing(self, provider, nhl_response):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload=nhl_response)
            events = await provider.get_events("nhl")

        # Second game: Seattle @ Edmonton, LIVE
        sea_edm = events[1]
        assert sea_edm.home_team == "Edmonton Oilers"
        assert sea_edm.away_team == "Seattle Kraken"
        assert "LIVE" in sea_edm.title
        assert "(2)" in sea_edm.title  # Seattle score
        assert "(3)" in sea_edm.title  # Edmonton score

    @pytest.mark.asyncio
    async def test_final_game_with_ot(self, provider, nhl_response):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload=nhl_response)
            events = await provider.get_events("nhl")

        # Third game: St. Louis @ Chicago, Final (OT)
        stl_chi = events[2]
        assert stl_chi.home_team == "Chicago Blackhawks"
        assert stl_chi.away_team == "St. Louis Blues"
        assert "Final" in stl_chi.title
        assert "(OT)" in stl_chi.title
        assert "(4)" in stl_chi.title  # Blues score
        assert "(3)" in stl_chi.title  # Hawks score

    @pytest.mark.asyncio
    async def test_non_nhl_category_returns_empty(self, provider):
        # Should not make any HTTP calls for non-NHL categories
        events = await provider.get_events("nba")
        assert events == []

    @pytest.mark.asyncio
    async def test_empty_game_week(self, provider):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", payload={"gameWeek": []})
            events = await provider.get_events("nhl")

        assert events == []


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_api_500(self, provider):
        with aioresponses() as m:
            m.get(f"{NHL_API_BASE}/schedule/now", status=500)

            with pytest.raises(RuntimeError, match="HTTP 500"):
                await provider.get_events("nhl")

    @pytest.mark.asyncio
    async def test_api_connection_error(self, provider):
        import aiohttp
        with aioresponses() as m:
            m.get(
                f"{NHL_API_BASE}/schedule/now",
                exception=aiohttp.ClientConnectionError("Connection refused"),
            )

            with pytest.raises(ConnectionError, match="NHL API"):
                await provider.get_events("nhl")


class TestTeamNameParsing:
    def test_standard_team(self, provider):
        team = {
            "placeName": {"default": "Seattle"},
            "commonName": {"default": "Kraken"},
            "abbrev": "SEA",
        }
        assert provider._team_full_name(team) == "Seattle Kraken"

    def test_two_word_place(self, provider):
        team = {
            "placeName": {"default": "St. Louis"},
            "commonName": {"default": "Blues"},
            "abbrev": "STL",
        }
        assert provider._team_full_name(team) == "St. Louis Blues"

    def test_two_word_name(self, provider):
        team = {
            "placeName": {"default": "Toronto"},
            "commonName": {"default": "Maple Leafs"},
            "abbrev": "TOR",
        }
        assert provider._team_full_name(team) == "Toronto Maple Leafs"

    def test_fallback_to_abbrev(self, provider):
        team = {"abbrev": "TOR"}
        assert provider._team_full_name(team) == "TOR"

    def test_missing_everything(self, provider):
        assert provider._team_full_name({}) is None
