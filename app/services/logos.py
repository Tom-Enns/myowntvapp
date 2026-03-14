import aiohttp
import logging
import urllib.parse

logger = logging.getLogger(__name__)

class LogoService:
    def __init__(self):
        # Cache to store team_name -> logo_url mapping
        self._cache: dict[str, str] = {}

    async def get_team_logo(self, team_name: str) -> str | None:
        """
        Fetches the official team logo from TheSportsDB public API.
        Results are cached to prevent rate-limiting and ensure fast UI responses.
        """
        clean_name = team_name.replace("(H)", "").replace("(A)", "").strip()
        if clean_name.startswith("St "):
            clean_name = clean_name.replace("St ", "St. ", 1)

        if clean_name in self._cache:
            return self._cache[clean_name]

        try:
            url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={urllib.parse.quote(clean_name)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        self._cache[clean_name] = None
                        return None
                    data = await resp.json()

            teams = data.get("teams", [])

            if teams and isinstance(teams, list) and len(teams) > 0:
                logo_url = teams[0].get("strBadge")
                if logo_url:
                    self._cache[clean_name] = logo_url
                    return logo_url

        except Exception as e:
            logger.warning(f"Failed to fetch logo for {clean_name}: {e}")

        # Cache failed lookups as None to prevent repeated failed requests
        self._cache[clean_name] = None
        return None

    async def get_logos_for_match(self, home_team: str, away_team: str) -> tuple[str | None, str | None]:
        """Fetch logos for both teams in parallel."""
        import asyncio
        home_logo, away_logo = await asyncio.gather(
            self.get_team_logo(home_team),
            self.get_team_logo(away_team),
        )
        return home_logo, away_logo
