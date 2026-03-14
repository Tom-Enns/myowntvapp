import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from pydantic import BaseModel
from typing import List, Optional

from app.services.logos import LogoService

logger = logging.getLogger(__name__)

class StreamEvent(BaseModel):
    id: str
    title: str
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    home_logo: Optional[str] = None
    away_logo: Optional[str] = None
    url: str
    category: str

class StreamScraper:
    def __init__(self, logo_service: LogoService):
        self.base_url = "https://thetvapp.to"
        self.logo_service = logo_service
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    async def scrape_category(self, category: str) -> List[StreamEvent]:
        """
        Scrape a specific sports category from thetvapp.to (e.g. 'nba', 'nhl').
        Returns a list of parsed StreamEvents including fetched team logos.
        """
        category_url = f"{self.base_url}/{category.lower().strip()}"
        events = []

        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(category_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        print(f"[scraper] Got {resp.status} for {category_url}")
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            # Find all the list-group items that represent events
            event_items = soup.find_all("a", class_="list-group-item")

            # Use asyncio.gather to fetch logos in parallel
            parse_tasks = []

            for item in event_items:
                href = item.get("href")
                title = item.text.strip()

                if href and (href.startswith("/event/") or (href.startswith("/tv/") and href != "/tv")):
                    full_url = f"{self.base_url}{href}"
                    # Create a unique ID from the slug
                    event_id = href.split("/")[-2] if len(href.split("/")) > 2 else href

                    parse_tasks.append(
                        self._parse_and_enrich_event(
                            event_id=event_id,
                            title=title,
                            url=full_url,
                            category=category
                        )
                    )

            # Await all parsed events
            if parse_tasks:
                results = await asyncio.gather(*parse_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, StreamEvent):
                        events.append(result)
                    else:
                        logger.warning(f"Failed to parse event: {result}")

            return events

        except Exception as e:
            print(f"[scraper] Failed to scrape category {category}: {type(e).__name__}: {e}")
            return []

    async def _parse_and_enrich_event(self, event_id: str, title: str, url: str, category: str) -> StreamEvent:
        """Helper to parse team names from titles and attach logos"""
        home_team = None
        away_team = None

        # The title often includes a newline and a datetime like "Boston Bruins @ NY Rangers\n    2026-..."
        lines = title.split("\n")
        clean_title = lines[0].strip()

        nice_time = ""
        if len(lines) > 1:
            dt_str = lines[1].strip()
            if dt_str.endswith('Z'):
                try:
                    import datetime
                    import zoneinfo
                    dt = datetime.datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                    dt_est = dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
                    def get_suffix(d):
                        return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')
                    nice_time = dt_est.strftime(f"\n%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
                except Exception:
                    pass

        final_title = clean_title + nice_time

        # Parse titles like "Team A @ Team B" or "Team A vs Team B"
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

        # If we successfully parsed teams, fetch their logos
        if home_team and away_team:
            home_logo, away_logo = await self.logo_service.get_logos_for_match(home_team, away_team)

        return StreamEvent(
            id=event_id,
            title=final_title,
            home_team=home_team,
            away_team=away_team,
            home_logo=home_logo,
            away_logo=away_logo,
            url=url,
            category=category
        )
