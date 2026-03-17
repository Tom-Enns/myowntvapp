"""Tests for the TheTVApp backend with mocked HTTP responses.

Uses HTML fixtures to test the parsing/extraction logic without
needing a live connection to thetvapp.to.
"""

import pytest
from aioresponses import aioresponses
from base64 import b64encode

from app.backends.thetvapp import TheTVAppBackend
from app.models import SportEvent
from tests.conftest import load_fixture


@pytest.fixture
def backend():
    return TheTVAppBackend()


class TestGetEventUrl:
    def test_thetvapp_event(self, backend, nba_event):
        url = backend._get_event_url(nba_event)
        assert url == "https://thetvapp.to/event/celtics-at-lakers-20260316/"

    def test_thetvapp_tv_channel(self, backend, tv_event):
        url = backend._get_event_url(tv_event)
        assert url == "https://thetvapp.to/tv/espn/"

    def test_non_thetvapp_event(self, backend, sportsdb_event):
        url = backend._get_event_url(sportsdb_event)
        assert url is None


class TestFindStreamInHtml:
    """Test stream extraction patterns via the resolver's find_stream_in_html."""

    def test_atob_pattern(self):
        from app.resolvers.generic import find_stream_in_html
        encoded = b64encode(b"https://stream.example.com/live/master.m3u8").decode()
        html = f"var url = atob('{encoded}');"
        assert find_stream_in_html(html) == "https://stream.example.com/live/master.m3u8"

    def test_source_pattern(self):
        from app.resolvers.generic import find_stream_in_html
        html = "source: 'https://cdn.example.com/hls/live/stream.m3u8',"
        assert find_stream_in_html(html) == "https://cdn.example.com/hls/live/stream.m3u8"

    def test_m3u8_url_pattern(self):
        from app.resolvers.generic import find_stream_in_html
        html = """file: 'https://live.example.com/hls/game.m3u8?token=abc'"""
        assert find_stream_in_html(html) == "https://live.example.com/hls/game.m3u8?token=abc"

    def test_playlist_load_pattern(self):
        from app.resolvers.generic import find_stream_in_html
        html = """var url = 'https://cdn.example.com/playlist/123/load/stream';"""
        assert find_stream_in_html(html) == "https://cdn.example.com/playlist/123/load/stream"

    def test_playlist_path_pattern(self):
        from app.resolvers.generic import find_stream_in_html
        html = """var url = 'https://cdn.example.com/live/playlist/stream.m3u8';"""
        assert find_stream_in_html(html) == "https://cdn.example.com/live/playlist/stream.m3u8"

    def test_no_stream_found(self):
        from app.resolvers.generic import find_stream_in_html
        html = "<html><body>No streams here</body></html>"
        assert find_stream_in_html(html) is None

    def test_atob_from_fixture(self):
        from app.resolvers.generic import find_stream_in_html
        html = load_fixture("iframe_with_atob_stream.html")
        result = find_stream_in_html(html)
        assert result == "https://stream.example.com/live/master.m3u8"

    def test_source_from_fixture(self):
        from app.resolvers.generic import find_stream_in_html
        html = load_fixture("iframe_with_source_stream.html")
        result = find_stream_in_html(html)
        assert result == "https://cdn.example.com/hls/live/stream.m3u8"

    def test_m3u8_from_fixture(self):
        from app.resolvers.generic import find_stream_in_html
        html = load_fixture("iframe_with_m3u8_url.html")
        result = find_stream_in_html(html)
        assert result == "https://live.example.com/hls/game123/playlist.m3u8?token=abc123"

    def test_no_stream_from_fixture(self):
        from app.resolvers.generic import find_stream_in_html
        html = load_fixture("iframe_no_stream.html")
        assert find_stream_in_html(html) is None


class TestSearchForEvent:
    @pytest.mark.asyncio
    async def test_finds_matching_event(self, backend, sportsdb_event):
        listing = load_fixture("thetvapp_nba_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            url = await backend._search_for_event(sportsdb_event)

        assert url == "https://thetvapp.to/event/celtics-at-lakers-20260316/"

    @pytest.mark.asyncio
    async def test_no_match(self, backend):
        event = SportEvent(
            event_id="sportsdb:999",
            title="Team X vs Team Y",
            category="nba",
            home_team="Team Y",
            away_team="Team X",
        )
        listing = load_fixture("thetvapp_nba_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            url = await backend._search_for_event(event)

        assert url is None

    @pytest.mark.asyncio
    async def test_no_teams(self, backend):
        event = SportEvent(event_id="sportsdb:1", title="Mystery Event", category="nba")
        url = await backend._search_for_event(event)
        assert url is None


class TestResolveStream:
    @pytest.mark.asyncio
    async def test_tv_channel_resolves(self, backend):
        """TV channel page → site handler → token endpoint → stream URL."""
        page_html = load_fixture("thetvapp_tv_channel_page.html")
        master = load_fixture("master_playlist.m3u8")

        event = SportEvent(
            event_id="thetvapp:espn",
            title="ESPN",
            category="tv",
        )

        with aioresponses() as m:
            m.get("https://thetvapp.to/tv/espn/", body=page_html)
            m.get(
                "https://thetvapp.to/token/espn_stream_001",
                payload={"url": "https://stream.example.com/espn/master.m3u8"},
            )
            m.get("https://stream.example.com/espn/master.m3u8", body=master)

            stream = await backend.resolve_stream(event)

        assert stream is not None
        assert stream.m3u8_url == "https://stream.example.com/espn/master.m3u8"
        assert stream.backend_id == "thetvapp"
        assert len(stream.qualities) == 3

    @pytest.mark.asyncio
    async def test_iframe_extraction_atob(self, backend):
        """Event page with iframe → iframe HTML with atob stream."""
        event_page = load_fixture("thetvapp_event_page_iframe.html")
        iframe_html = load_fixture("iframe_with_atob_stream.html")
        master = load_fixture("master_playlist.m3u8")

        event = SportEvent(
            event_id="thetvapp:test",
            title="Test Game",
            category="nba",
        )

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", body=event_page)
            m.get("https://embed.example.com/embed/stream123", body=iframe_html)
            m.get("https://stream.example.com/live/master.m3u8", body=master)

            stream = await backend.resolve_stream(event)

        assert stream is not None
        assert stream.m3u8_url == "https://stream.example.com/live/master.m3u8"
        assert stream.headers["Referer"] == "https://embed.example.com/embed/stream123"

    @pytest.mark.asyncio
    async def test_iframe_no_stream_returns_none(self, backend):
        """Event page with iframe but no stream URL should return None."""
        event_page = load_fixture("thetvapp_event_page_iframe.html")
        iframe_html = load_fixture("iframe_no_stream.html")

        event = SportEvent(
            event_id="thetvapp:test",
            title="Test Game",
            category="nba",
        )

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", body=event_page)
            m.get("https://embed.example.com/embed/stream123", body=iframe_html)

            stream = await backend.resolve_stream(event)
            assert stream is None

    @pytest.mark.asyncio
    async def test_thetvapp_event_resolves(self, backend, nba_event):
        """Full resolve: event_id → URL → extract stream."""
        event_page = load_fixture("thetvapp_event_page_iframe.html")
        iframe_html = load_fixture("iframe_with_source_stream.html")
        master = load_fixture("master_playlist.m3u8")

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/celtics-at-lakers-20260316/", body=event_page)
            m.get("https://embed.example.com/embed/stream123", body=iframe_html)
            m.get("https://cdn.example.com/hls/live/stream.m3u8", body=master)

            stream = await backend.resolve_stream(nba_event)

        assert stream is not None
        assert "cdn.example.com" in stream.m3u8_url

    @pytest.mark.asyncio
    async def test_sportsdb_event_searches_then_resolves(self, backend, sportsdb_event):
        """Non-thetvapp event: search → find match → extract stream."""
        listing = load_fixture("thetvapp_nba_listing.html")
        event_page = load_fixture("thetvapp_event_page_iframe.html")
        iframe_html = load_fixture("iframe_with_m3u8_url.html")
        master = load_fixture("master_playlist.m3u8")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            m.get("https://thetvapp.to/event/celtics-at-lakers-20260316/", body=event_page)
            m.get("https://embed.example.com/embed/stream123", body=iframe_html)
            m.get("https://live.example.com/hls/game123/playlist.m3u8?token=abc123", body=master)

            stream = await backend.resolve_stream(sportsdb_event)

        assert stream is not None

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, backend):
        event = SportEvent(
            event_id="sportsdb:999",
            title="Nobody vs Nobody",
            category="nba",
            home_team="Nobody",
            away_team="Also Nobody",
        )
        listing = load_fixture("thetvapp_nba_listing.html")

        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            stream = await backend.resolve_stream(event)

        assert stream is None


class TestDiscoverLinks:
    @pytest.mark.asyncio
    async def test_discover_returns_link(self, backend, nba_event):
        """discover_links returns a StreamLink for a thetvapp event."""
        links = await backend.discover_links(nba_event)
        assert len(links) == 1
        assert links[0].url == "https://thetvapp.to/event/celtics-at-lakers-20260316/"
        assert links[0].backend_id == "thetvapp"

    @pytest.mark.asyncio
    async def test_discover_searches_for_sportsdb_event(self, backend, sportsdb_event):
        listing = load_fixture("thetvapp_nba_listing.html")
        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            links = await backend.discover_links(sportsdb_event)

        assert len(links) == 1
        assert "celtics-at-lakers" in links[0].url

    @pytest.mark.asyncio
    async def test_discover_no_match_returns_empty(self, backend):
        event = SportEvent(
            event_id="sportsdb:999",
            title="Nobody vs Nobody",
            category="nba",
            home_team="Nobody",
            away_team="Also Nobody",
        )
        listing = load_fixture("thetvapp_nba_listing.html")
        with aioresponses() as m:
            m.get("https://thetvapp.to/nba", body=listing)
            links = await backend.discover_links(event)
        assert links == []
