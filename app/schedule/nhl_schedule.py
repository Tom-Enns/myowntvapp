"""NHL Official API schedule provider — fetches game schedules from api-web.nhle.com.

Uses the NHL's public (no auth) web API, the same one nhl.com uses.
Returns the full current week's schedule with official team logos.
"""

import logging
from datetime import datetime, timezone

import aiohttp

from app.models import SportEvent
from app.schedule.base import ScheduleProvider

logger = logging.getLogger(__name__)

NHL_API_BASE = "https://api-web.nhle.com/v1"


class NHLSchedule(ScheduleProvider):
    """Fetches NHL game schedules from the official NHL web API."""

    @property
    def provider_id(self) -> str:
        return "nhl"

    @property
    def display_name(self) -> str:
        return "NHL Official"

    def supported_categories(self) -> list[str]:
        return ["nhl"]

    async def get_events(self, category: str) -> list[SportEvent]:
        if category.lower() != "nhl":
            return []

        url = f"{NHL_API_BASE}/schedule/now"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"NHL API returned HTTP {resp.status}. The API may be temporarily unavailable."
                        )
                    data = await resp.json()
        except aiohttp.ClientConnectionError as e:
            raise ConnectionError(
                f"Cannot connect to NHL API (api-web.nhle.com). ({e})"
            )
        except aiohttp.ClientConnectorError as e:
            raise ConnectionError(
                f"Cannot connect to NHL API (api-web.nhle.com). ({e})"
            )
        except TimeoutError:
            raise TimeoutError(
                "NHL API did not respond within 15 seconds."
            )

        events = []
        game_week = data.get("gameWeek", [])

        for day in game_week:
            for game in day.get("games", []):
                event = self._parse_game(game)
                if event:
                    events.append(event)

        return events

    def _parse_game(self, game: dict) -> SportEvent | None:
        """Parse a single game object from the NHL API response."""
        try:
            away = game.get("awayTeam", {})
            home = game.get("homeTeam", {})

            away_name = self._team_full_name(away)
            home_name = self._team_full_name(home)

            if not away_name or not home_name:
                return None

            game_state = game.get("gameState", "FUT")
            game_id = game.get("id", 0)

            # Parse start time
            start_time = None
            start_utc = game.get("startTimeUTC")
            if start_utc:
                try:
                    start_time = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Build title
            title = f"{away_name} @ {home_name}"

            # Add score for live/finished games
            if game_state in ("LIVE", "CRIT"):
                away_score = away.get("score", 0)
                home_score = home.get("score", 0)
                title = f"{away_name} ({away_score}) @ {home_name} ({home_score}) — LIVE"
            elif game_state in ("OFF", "FINAL"):
                away_score = away.get("score", 0)
                home_score = home.get("score", 0)
                period_type = ""
                outcome = game.get("gameOutcome", {})
                if outcome:
                    lpt = outcome.get("lastPeriodType", "")
                    if lpt == "OT":
                        period_type = " (OT)"
                    elif lpt == "SO":
                        period_type = " (SO)"
                title = f"{away_name} ({away_score}) @ {home_name} ({home_score}) — Final{period_type}"

            # Add formatted time for future games
            if game_state == "FUT" and start_time:
                try:
                    import zoneinfo
                    dt_est = start_time.astimezone(zoneinfo.ZoneInfo("America/New_York"))

                    def get_suffix(d):
                        return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')

                    title += dt_est.strftime(f"\n%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
                except Exception:
                    pass

            # Official logos from NHL API
            home_logo = home.get("logo")
            away_logo = away.get("logo")

            return SportEvent(
                event_id=f"nhl:{game_id}",
                title=title,
                category="nhl",
                start_time=start_time,
                home_team=home_name,
                away_team=away_name,
                home_logo=home_logo,
                away_logo=away_logo,
            )
        except Exception as e:
            logger.warning(f"Failed to parse NHL game: {e}")
            return None

    @staticmethod
    def _team_full_name(team: dict) -> str | None:
        """Build full team name from NHL API team object.

        Combines placeName and commonName, e.g. "Seattle" + "Kraken" = "Seattle Kraken".
        """
        place = team.get("placeName", {})
        common = team.get("commonName", {})

        place_name = place.get("default", "") if isinstance(place, dict) else str(place)
        common_name = common.get("default", "") if isinstance(common, dict) else str(common)

        if place_name and common_name:
            return f"{place_name} {common_name}"
        # Fallback to abbreviation
        return team.get("abbrev")


def create_provider() -> NHLSchedule:
    return NHLSchedule()
