"""TheSportsDB schedule provider — fetches real sports schedules from the free API."""

import logging
from datetime import datetime, timezone

import aiohttp

from app.models import SportEvent
from app.services.logos import LogoService
from app.schedule.base import ScheduleProvider

logger = logging.getLogger(__name__)

# Map our categories to TheSportsDB league IDs
LEAGUE_MAP = {
    "nba": "4387",    # NBA
    "nhl": "4380",    # NHL
    "mlb": "4424",    # MLB
    "nfl": "4391",    # NFL
    "ncaaf": "4479",  # NCAA Football (FBS)
    "ncaab": "4607",  # NCAA Basketball
    "soccer": "4328", # English Premier League (default soccer league)
}


class TheSportsDBSchedule(ScheduleProvider):
    """Fetches upcoming schedules from TheSportsDB free API."""

    BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"

    def __init__(self, logo_service: LogoService):
        self.logo_service = logo_service

    @property
    def provider_id(self) -> str:
        return "sportsdb"

    @property
    def display_name(self) -> str:
        return "TheSportsDB"

    def supported_categories(self) -> list[str]:
        return list(LEAGUE_MAP.keys())

    async def get_events(self, category: str) -> list[SportEvent]:
        league_id = LEAGUE_MAP.get(category.lower())
        if not league_id:
            return []

        try:
            url = f"{self.BASE_URL}/eventsnextleague.php?id={league_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"TheSportsDB returned {resp.status} for {category}")
                        return []
                    data = await resp.json()

            raw_events = data.get("events") or []
            events = []

            for ev in raw_events:
                home_team = ev.get("strHomeTeam", "")
                away_team = ev.get("strAwayTeam", "")
                event_name = ev.get("strEvent", f"{home_team} vs {away_team}")

                # Parse start time
                start_time = None
                date_str = ev.get("strTimestamp") or ev.get("dateEvent")
                if date_str:
                    try:
                        if "T" in str(date_str):
                            start_time = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                        else:
                            start_time = datetime.strptime(str(date_str), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except Exception:
                        pass

                # Build nice title with time
                title = f"{away_team} @ {home_team}" if home_team and away_team else event_name
                if start_time:
                    try:
                        import zoneinfo
                        dt_est = start_time.astimezone(zoneinfo.ZoneInfo("America/New_York"))

                        def get_suffix(d):
                            return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')

                        title += dt_est.strftime(f"\n%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
                    except Exception:
                        pass

                # Get logos
                home_logo = ev.get("strHomeTeamBadge")
                away_logo = ev.get("strAwayTeamBadge")

                # If badges not in event data, fetch from search API
                if not home_logo or not away_logo:
                    try:
                        h_logo, a_logo = await self.logo_service.get_logos_for_match(home_team, away_team)
                        home_logo = home_logo or h_logo
                        away_logo = away_logo or a_logo
                    except Exception:
                        pass

                event_id = f"sportsdb:{ev.get('idEvent', event_name)}"

                events.append(SportEvent(
                    event_id=event_id,
                    title=title,
                    category=category,
                    start_time=start_time,
                    home_team=home_team,
                    away_team=away_team,
                    home_logo=home_logo,
                    away_logo=away_logo,
                ))

            return events

        except Exception as e:
            logger.error(f"TheSportsDB failed for {category}: {e}")
            return []


def create_provider(logo_service: LogoService) -> TheSportsDBSchedule:
    return TheSportsDBSchedule(logo_service)
