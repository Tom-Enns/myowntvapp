"""ESPN API schedule provider — fetches game schedules for NBA, MLB, NFL, and NHL.

Uses ESPN's public (no auth) scoreboard API, which provides a consistent
format across all sports with logos, scores, game status, and period detail.
"""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from app.models import SportEvent
from app.schedule.base import ScheduleProvider

logger = logging.getLogger(__name__)

ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Maps our internal category names to ESPN sport/league paths
CATEGORY_MAP = {
    "nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "nfl": "football/nfl",
    "nhl": "hockey/nhl",
}


class ESPNSchedule(ScheduleProvider):
    """Fetches game schedules from the ESPN public API."""

    @property
    def provider_id(self) -> str:
        return "espn"

    @property
    def display_name(self) -> str:
        return "ESPN"

    def supported_categories(self) -> list[str]:
        return list(CATEGORY_MAP.keys())

    async def get_events(self, category: str) -> list[SportEvent]:
        cat = category.lower()
        espn_path = CATEGORY_MAP.get(cat)
        if not espn_path:
            return []

        # Fetch a week of games
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=6)
        date_range = f"{today.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

        url = f"{ESPN_API_BASE}/{espn_path}/scoreboard?dates={date_range}&limit=200"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 403:
                        raise RuntimeError(
                            f"ESPN API returned 403 Forbidden for {cat.upper()}. Your IP may be blocked."
                        )
                    if resp.status != 200:
                        raise RuntimeError(
                            f"ESPN API returned HTTP {resp.status} for {cat.upper()}."
                        )
                    data = await resp.json()
        except aiohttp.ClientConnectionError as e:
            raise ConnectionError(f"Cannot connect to ESPN API. ({e})")
        except TimeoutError:
            raise TimeoutError("ESPN API did not respond within 15 seconds.")

        events = []
        for espn_event in data.get("events", []):
            event = self._parse_event(espn_event, cat)
            if event:
                events.append(event)

        return events

    def _parse_event(self, espn_event: dict, category: str) -> SportEvent | None:
        """Parse a single ESPN event into a SportEvent."""
        try:
            competitions = espn_event.get("competitions", [])
            if not competitions:
                return None

            comp = competitions[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                return None

            # ESPN lists competitors with homeAway field
            home = away = None
            for c in competitors:
                if c.get("homeAway") == "home":
                    home = c
                elif c.get("homeAway") == "away":
                    away = c

            if not home or not away:
                return None

            home_team = home["team"]["displayName"]
            away_team = away["team"]["displayName"]
            home_logo = home["team"].get("logo")
            away_logo = away["team"].get("logo")

            # Parse game status: pre, in, post
            status = comp.get("status", {}).get("type", {})
            state = status.get("state", "pre")
            short_detail = status.get("shortDetail", "")

            event_id = espn_event.get("id", "")
            start_time = None
            date_str = espn_event.get("date")
            if date_str:
                try:
                    start_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Build title based on game state
            title = f"{away_team} @ {home_team}"

            if state == "in":
                away_score = away.get("score", "0")
                home_score = home.get("score", "0")
                title = f"{away_team} ({away_score}) @ {home_team} ({home_score}) — LIVE"
                if short_detail:
                    title += f" ({short_detail})"
            elif state == "post":
                away_score = away.get("score", "0")
                home_score = home.get("score", "0")
                detail = short_detail if short_detail and "Final" not in short_detail else ""
                suffix = f" ({detail})" if detail else ""
                title = f"{away_team} ({away_score}) @ {home_team} ({home_score}) — Final{suffix}"
            elif state == "pre" and start_time:
                try:
                    import zoneinfo
                    dt_est = start_time.astimezone(zoneinfo.ZoneInfo("America/New_York"))

                    def get_suffix(d):
                        return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')

                    title += dt_est.strftime(f"\n%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
                except Exception:
                    pass

            return SportEvent(
                event_id=f"{category}:{event_id}",
                title=title,
                category=category,
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                home_logo=home_logo,
                away_logo=away_logo,
            )
        except Exception as e:
            logger.warning(f"Failed to parse ESPN event: {e}")
            return None


def create_provider() -> ESPNSchedule:
    return ESPNSchedule()
