"""Shared fixtures for the test suite."""

import pathlib
from unittest.mock import AsyncMock

import pytest

from app.models import SportEvent, ResolvedStream, StreamQuality

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Load a text fixture file by name."""
    return (FIXTURES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Common model fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def nba_event():
    return SportEvent(
        event_id="thetvapp:celtics-at-lakers-20260316",
        title="Boston Celtics @ Los Angeles Lakers",
        category="nba",
        home_team="Los Angeles Lakers",
        away_team="Boston Celtics",
    )


@pytest.fixture
def tv_event():
    return SportEvent(
        event_id="thetvapp:espn",
        title="ESPN",
        category="tv",
    )


@pytest.fixture
def sportsdb_event():
    """An event from TheSportsDB (no thetvapp: prefix)."""
    return SportEvent(
        event_id="sportsdb:12345",
        title="Boston Celtics vs Los Angeles Lakers",
        category="nba",
        home_team="Los Angeles Lakers",
        away_team="Boston Celtics",
    )


@pytest.fixture
def sample_stream():
    return ResolvedStream(
        backend_id="thetvapp",
        backend_name="TheTVApp.to",
        m3u8_url="https://stream.example.com/live/master.m3u8",
        headers={"Referer": "https://thetvapp.to/"},
        qualities=[
            StreamQuality(resolution="1080p", width=1920, height=1080, bandwidth=6000000),
            StreamQuality(resolution="720p", width=1280, height=720, bandwidth=3000000),
        ],
    )


# ---------------------------------------------------------------------------
# Mock logo service
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_logo_service():
    logo = AsyncMock()
    logo.get_logos_for_match = AsyncMock(return_value=(
        "https://logos.example.com/lakers.png",
        "https://logos.example.com/celtics.png",
    ))
    logo.get_team_logo = AsyncMock(return_value="https://logos.example.com/team.png")
    return logo
