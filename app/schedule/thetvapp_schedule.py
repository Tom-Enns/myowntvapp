"""TheTVApp.to schedule provider — extracts event listings from thetvapp.to."""

import asyncio
import logging

import aiohttp
from bs4 import BeautifulSoup

from app.models import SportEvent
from app.services.logos import LogoService
from app.schedule.base import ScheduleProvider

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

CATEGORIES = ["nba", "mlb", "nhl", "nfl", "ncaaf", "ncaab", "soccer", "ppv", "tv"]


class TheTVAppSchedule(ScheduleProvider):
    """Uses thetvapp.to listing pages as a schedule source."""

    def __init__(self, logo_service: LogoService):
        self.base_url = "https://thetvapp.to"
        self.logo_service = logo_service

    @property
    def provider_id(self) -> str:
        return "thetvapp"

    @property
    def display_name(self) -> str:
        return "TheTVApp.to"

    def supported_categories(self) -> list[str]:
        return list(CATEGORIES)

    async def get_events(self, category: str) -> list[SportEvent]:
        category_url = f"{self.base_url}/{category.lower().strip()}"
        events = []

        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(category_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 403:
                        raise PermissionError(
                            f"TheTVApp.to returned 403 Forbidden. "
                            f"Your IP address may be blocked by this provider."
                        )
                    if resp.status != 200:
                        raise RuntimeError(
                            f"TheTVApp.to returned HTTP {resp.status} for {category}."
                        )
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            event_items = soup.find_all("a", class_="list-group-item")

            tasks = []
            for item in event_items:
                href = item.get("href")
                title = item.text.strip()

                if href and (href.startswith("/event/") or (href.startswith("/tv/") and href != "/tv")):
                    full_url = f"{self.base_url}{href}"
                    event_id = href.split("/")[-2] if len(href.split("/")) > 2 else href

                    tasks.append(self._parse_event(event_id, title, full_url, category))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, SportEvent):
                        events.append(result)
                    else:
                        logger.warning(f"Failed to parse event: {result}")

            return events

        except (PermissionError, RuntimeError):
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError) as e:
            raise ConnectionError(
                f"Cannot connect to TheTVApp.to — the site may be down or your network is blocking it. ({e})"
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"TheTVApp.to did not respond within 20 seconds. The site may be overloaded or unreachable."
            )
        except Exception as e:
            raise RuntimeError(f"TheTVApp.to error: {type(e).__name__}: {e}")

    async def _parse_event(self, event_id: str, title: str, url: str, category: str) -> SportEvent:
        """Parse team names from title and fetch logos."""
        lines = title.split("\n")
        clean_title = lines[0].strip()
        start_time = None

        nice_time = ""
        if len(lines) > 1:
            dt_str = lines[1].strip()
            if dt_str.endswith('Z'):
                try:
                    import datetime
                    import zoneinfo
                    dt = datetime.datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                    start_time = dt
                    dt_est = dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))

                    def get_suffix(d):
                        return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')

                    nice_time = dt_est.strftime(f"\n%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
                except Exception:
                    pass

        final_title = clean_title + nice_time

        home_team = None
        away_team = None

        if " @ " in clean_title:
            parts = clean_title.split(" @ ")
            away_team = parts[0].strip()
            home_team = parts[1].strip()
        elif " vs " in clean_title.lower():
            parts = clean_title.lower().split(" vs ")
            home_team = parts[0].strip().title()
            away_team = parts[1].strip().title()

        home_logo = None
        away_logo = None
        if home_team and away_team:
            home_logo, away_logo = await self.logo_service.get_logos_for_match(home_team, away_team)

        # Store the thetvapp URL in event_id so the backend can use it
        return SportEvent(
            event_id=f"thetvapp:{event_id}",
            title=final_title,
            category=category,
            start_time=start_time,
            home_team=home_team,
            away_team=away_team,
            home_logo=home_logo,
            away_logo=away_logo,
        )

    def get_event_url(self, event: SportEvent) -> str | None:
        """Get the thetvapp.to URL for an event created by this provider."""
        if event.event_id.startswith("thetvapp:"):
            slug = event.event_id.removeprefix("thetvapp:")
            # Determine if it's a TV channel or event
            if event.category == "tv":
                return f"{self.base_url}/tv/{slug}/"
            return f"{self.base_url}/event/{slug}/"
        return None


def create_provider(logo_service: LogoService) -> TheTVAppSchedule:
    return TheTVAppSchedule(logo_service)
