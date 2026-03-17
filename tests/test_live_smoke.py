"""Live smoke tests — hit real external services.

These are SKIPPED by default. Run them with:
    pytest -m live -v

They verify that real APIs are reachable and returning parseable data,
so you can catch site changes or API breakage early.
"""

import pytest

from app.backends.thetvapp import TheTVAppBackend
from app.schedule.thetvapp_schedule import TheTVAppSchedule
from app.schedule.nhl_schedule import NHLSchedule
from app.schedule.sportsdb import TheSportsDBSchedule
from app.services.logos import LogoService

pytestmark = pytest.mark.live


@pytest.fixture
def backend():
    return TheTVAppBackend()


@pytest.fixture
def logo_service():
    return LogoService()


@pytest.fixture
def thetvapp_schedule(logo_service):
    return TheTVAppSchedule(logo_service)


@pytest.fixture
def nhl_schedule():
    return NHLSchedule()


@pytest.fixture
def sportsdb_schedule(logo_service):
    return TheSportsDBSchedule(logo_service)


# ---------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------

class TestLiveHealthCheck:
    @pytest.mark.asyncio
    async def test_thetvapp_reachable(self, backend):
        """Verify thetvapp.to is up and responding."""
        healthy = await backend.health_check()
        assert healthy, "thetvapp.to is not reachable — site may be down or your IP is blocked"


# ---------------------------------------------------------------
# NHL Official API
# ---------------------------------------------------------------

class TestLiveNHLSchedule:
    @pytest.mark.asyncio
    async def test_nhl_api_returns_games(self, nhl_schedule):
        """NHL API should return at least some games for the current week."""
        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API — network issue")

        # During regular season there should be games; off-season may be empty
        if not events:
            pytest.skip("No NHL games this week (may be off-season)")

        print(f"\n  NHL API returned {len(events)} games this week:")
        for ev in events[:5]:
            print(f"    {ev.title.split(chr(10))[0]}")

        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_nhl_games_have_official_logos(self, nhl_schedule):
        """Every NHL game should come with official SVG logos."""
        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API")

        if not events:
            pytest.skip("No NHL games this week")

        for ev in events:
            assert ev.home_logo, f"Missing home logo for: {ev.title}"
            assert ev.away_logo, f"Missing away logo for: {ev.title}"
            assert "nhle.com" in ev.home_logo, f"Home logo not from NHL CDN: {ev.home_logo}"
            assert ev.home_logo.endswith(".svg"), f"Home logo not SVG: {ev.home_logo}"

        print(f"\n  All {len(events)} games have official NHL SVG logos")

    @pytest.mark.asyncio
    async def test_nhl_games_have_team_names(self, nhl_schedule):
        """Every game should have parseable team names."""
        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API")

        if not events:
            pytest.skip("No NHL games this week")

        for ev in events:
            assert ev.home_team, f"Missing home team for: {ev.event_id}"
            assert ev.away_team, f"Missing away team for: {ev.event_id}"
            # Team names should have at least two words (city + name)
            assert " " in ev.home_team, f"Unexpected home team format: {ev.home_team}"
            assert " " in ev.away_team, f"Unexpected away team format: {ev.away_team}"

    @pytest.mark.asyncio
    async def test_nhl_games_have_start_times(self, nhl_schedule):
        """Future games should have start times."""
        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API")

        future_events = [ev for ev in events if "LIVE" not in ev.title and "Final" not in ev.title]
        if not future_events:
            pytest.skip("No future NHL games to check")

        for ev in future_events:
            assert ev.start_time is not None, f"Missing start_time for future game: {ev.title}"

    @pytest.mark.asyncio
    async def test_nhl_event_ids_are_unique(self, nhl_schedule):
        """Event IDs should be unique across the week."""
        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API")

        if not events:
            pytest.skip("No NHL games this week")

        ids = [ev.event_id for ev in events]
        assert len(ids) == len(set(ids)), f"Duplicate event IDs found: {[x for x in ids if ids.count(x) > 1]}"


# ---------------------------------------------------------------
# TheSportsDB
# ---------------------------------------------------------------

class TestLiveSportsDB:
    @pytest.mark.asyncio
    async def test_sportsdb_returns_nhl_events(self, sportsdb_schedule):
        """TheSportsDB should return NHL events (as fallback validation)."""
        try:
            events = await sportsdb_schedule.get_events("nhl")
        except Exception:
            pytest.skip("Cannot reach TheSportsDB API")

        if not events:
            pytest.skip("TheSportsDB returned no NHL events (may be off-season)")

        print(f"\n  TheSportsDB returned {len(events)} NHL events")
        for ev in events[:3]:
            print(f"    {ev.title.split(chr(10))[0]}")


# ---------------------------------------------------------------
# Logo Service
# ---------------------------------------------------------------

class TestLiveLogos:
    @pytest.mark.asyncio
    async def test_logo_for_known_team(self, logo_service):
        """Should find a logo for a well-known team."""
        logo = await logo_service.get_team_logo("Boston Bruins")
        assert logo is not None, "No logo found for Boston Bruins"
        assert logo.startswith("http"), f"Logo URL looks wrong: {logo}"
        print(f"\n  Boston Bruins logo: {logo}")

    @pytest.mark.asyncio
    async def test_logos_for_match(self, logo_service):
        """Should find logos for both teams in a matchup."""
        home_logo, away_logo = await logo_service.get_logos_for_match(
            "Toronto Maple Leafs", "Montreal Canadiens"
        )
        assert home_logo is not None, "No logo found for Toronto Maple Leafs"
        assert away_logo is not None, "No logo found for Montreal Canadiens"
        print(f"\n  Maple Leafs: {home_logo}")
        print(f"  Canadiens: {away_logo}")

    @pytest.mark.asyncio
    async def test_logo_caching(self, logo_service):
        """Second lookup should use cache."""
        logo1 = await logo_service.get_team_logo("Chicago Blackhawks")
        logo2 = await logo_service.get_team_logo("Chicago Blackhawks")
        assert logo1 == logo2
        assert "Chicago Blackhawks" in logo_service._cache


# ---------------------------------------------------------------
# TheTVApp.to Schedule
# ---------------------------------------------------------------

class TestLiveTheTVAppSchedule:
    @pytest.mark.asyncio
    async def test_tv_channels_available(self, thetvapp_schedule):
        """TV channels should always be listed (not dependent on live games)."""
        try:
            events = await thetvapp_schedule.get_events("tv")
        except PermissionError:
            pytest.skip("IP is blocked by thetvapp.to")

        assert len(events) > 0, "No TV channels found — thetvapp.to listing may have changed"
        for ev in events:
            assert ev.event_id.startswith("thetvapp:")
            assert ev.category == "tv"
            assert ev.title

        print(f"\n  TheTVApp.to has {len(events)} TV channels:")
        for ev in events[:5]:
            print(f"    {ev.title}")

    @pytest.mark.asyncio
    async def test_any_sport_has_events(self, thetvapp_schedule):
        """At least one sport category should have events."""
        categories = ["nba", "mlb", "nhl", "nfl", "soccer"]
        found_any = False
        for cat in categories:
            try:
                events = await thetvapp_schedule.get_events(cat)
                if events:
                    found_any = True
                    print(f"\n  TheTVApp.to {cat}: {len(events)} events")
                    break
            except Exception:
                continue

        if not found_any:
            pytest.skip("No live sports events found across any category (may be off-season)")


# ---------------------------------------------------------------
# Stream Resolution
# ---------------------------------------------------------------

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

        print(f"\n  ESPN stream resolved: {stream.m3u8_url[:80]}...")
        if stream.qualities:
            print(f"  Qualities: {', '.join(q.label for q in stream.qualities)}")


# ---------------------------------------------------------------
# Full Integration: Schedule → Logo check
# ---------------------------------------------------------------

class TestLiveIntegration:
    @pytest.mark.asyncio
    async def test_nhl_schedule_logos_are_loadable(self, nhl_schedule):
        """Verify that NHL logo URLs actually return images (not 404s)."""
        import aiohttp

        try:
            events = await nhl_schedule.get_events("nhl")
        except ConnectionError:
            pytest.skip("Cannot reach NHL API")

        if not events:
            pytest.skip("No NHL games this week")

        # Just check 2 logos to avoid hammering the CDN
        ev = events[0]
        async with aiohttp.ClientSession() as session:
            for label, url in [("home", ev.home_logo), ("away", ev.away_logo)]:
                if not url:
                    continue
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    assert resp.status == 200, f"{label} logo returned {resp.status}: {url}"
                    content_type = resp.headers.get("content-type", "")
                    assert "svg" in content_type or "image" in content_type, \
                        f"{label} logo wrong content-type: {content_type}"

        print(f"\n  Logo URLs for {ev.away_team} @ {ev.home_team} are valid and loadable")
