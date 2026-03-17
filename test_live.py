#!/usr/bin/env python3
"""Quick live integration check — run this to verify all providers and logos work.

Usage:
    python test_live.py          # Check all providers
    python test_live.py nhl      # Check NHL only
    python test_live.py logos     # Check logo service only

This hits real APIs so it requires internet access.
"""

import asyncio
import sys

import aiohttp

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")
def info(msg): print(f"  {DIM}{msg}{RESET}")

passed = 0
failed = 0
skipped = 0

def check(condition, pass_msg, fail_msg):
    global passed, failed
    if condition:
        ok(pass_msg)
        passed += 1
    else:
        fail(fail_msg)
        failed += 1

# ---------------------------------------------------------------
# Checks
# ---------------------------------------------------------------

async def check_nhl():
    from app.schedule.nhl_schedule import NHLSchedule
    global skipped

    print(f"\n{BOLD}NHL Official API (api-web.nhle.com){RESET}")

    provider = NHLSchedule()
    try:
        events = await provider.get_events("nhl")
    except Exception as e:
        fail(f"NHL API error: {e}")
        return

    check(len(events) > 0, f"Got {len(events)} games this week", "No games returned (off-season?)")

    if not events:
        skipped += 1
        return

    # Check structure
    for ev in events:
        check(ev.home_team and " " in ev.home_team,
              f"Teams parsed: {ev.away_team} @ {ev.home_team}",
              f"Bad team name: home={ev.home_team} away={ev.away_team}")
        break  # Just check first one

    # Check logos
    logos_ok = all(ev.home_logo and ev.away_logo for ev in events)
    check(logos_ok, f"All {len(events)} games have logos", "Some games missing logos")

    # Verify a logo URL actually loads
    ev = events[0]
    if ev.home_logo:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(ev.home_logo, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    check(resp.status == 200,
                          f"Logo URL loads: {ev.home_logo[:60]}...",
                          f"Logo URL returned {resp.status}")
        except Exception as e:
            fail(f"Logo URL failed to load: {e}")

    # Check start times on future games
    future = [ev for ev in events if "LIVE" not in ev.title and "Final" not in ev.title]
    if future:
        has_times = all(ev.start_time for ev in future)
        check(has_times, f"{len(future)} future games have start times", "Some future games missing start times")

    # Print schedule
    print(f"\n  {DIM}Schedule:{RESET}")
    for ev in events:
        line = ev.title.split('\n')[0]
        state = ""
        if "LIVE" in ev.title:
            state = f" {RED}LIVE{RESET}"
        elif "Final" in ev.title:
            state = f" {DIM}Final{RESET}"
        info(f"  {line}{state}")


async def check_sportsdb():
    from app.schedule.sportsdb import TheSportsDBSchedule
    from app.services.logos import LogoService

    print(f"\n{BOLD}TheSportsDB API{RESET}")

    provider = TheSportsDBSchedule(LogoService())
    for cat in ["nhl", "nba"]:
        try:
            events = await provider.get_events(cat)
            check(len(events) > 0, f"{cat.upper()}: {len(events)} events", f"{cat.upper()}: no events")
            if events:
                info(f"  First: {events[0].title.split(chr(10))[0]}")
        except Exception as e:
            fail(f"{cat.upper()}: {e}")


async def check_thetvapp():
    from app.schedule.thetvapp_schedule import TheTVAppSchedule
    from app.services.logos import LogoService
    global skipped

    print(f"\n{BOLD}TheTVApp.to{RESET}")

    provider = TheTVAppSchedule(LogoService())
    try:
        events = await provider.get_events("tv")
        check(len(events) > 0, f"TV channels: {len(events)} found", "No TV channels found")
        if events:
            info(f"  Channels: {', '.join(ev.title.split(chr(10))[0] for ev in events[:5])}...")
    except PermissionError:
        fail("IP is BLOCKED by thetvapp.to (403)")
        warn("Your brother's scenario — use a VPN or different network")
        return
    except Exception as e:
        fail(f"Error: {e}")
        return

    # Try a sport category
    for cat in ["nhl", "nba", "mlb"]:
        try:
            events = await provider.get_events(cat)
            if events:
                check(True, f"{cat.upper()}: {len(events)} events", "")
                break
        except Exception:
            continue


async def check_logos():
    from app.services.logos import LogoService

    print(f"\n{BOLD}Logo Service (TheSportsDB){RESET}")

    service = LogoService()

    teams = ["Boston Bruins", "Toronto Maple Leafs", "Los Angeles Lakers", "New York Rangers"]
    for team in teams:
        logo = await service.get_team_logo(team)
        check(logo is not None, f"{team}: {logo[:50]}..." if logo else "", f"{team}: no logo found")

    # Test caching
    logo1 = await service.get_team_logo("Boston Bruins")
    logo2 = await service.get_team_logo("Boston Bruins")
    check(logo1 == logo2 and "Boston Bruins" in service._cache, "Caching works", "Cache not working")


async def check_backend():
    from app.backends.thetvapp import TheTVAppBackend

    print(f"\n{BOLD}TheTVApp Backend (health check){RESET}")

    backend = TheTVAppBackend()
    healthy = await backend.health_check()
    check(healthy, "thetvapp.to is reachable", "thetvapp.to is NOT reachable (blocked or down)")


async def check_registry_integration():
    from app.schedule.registry import ScheduleRegistry
    from app.schedule.nhl_schedule import NHLSchedule
    from app.schedule.thetvapp_schedule import TheTVAppSchedule
    from app.schedule.sportsdb import TheSportsDBSchedule
    from app.services.logos import LogoService

    print(f"\n{BOLD}Full Registry Integration{RESET}")

    logos = LogoService()
    reg = ScheduleRegistry()
    reg.register(TheTVAppSchedule(logos))
    reg.register(TheSportsDBSchedule(logos))
    reg.register(NHLSchedule())
    reg.set_primary("thetvapp")

    result = await reg.get_events_with_status("nhl")
    check(len(result.events) > 0, f"NHL via registry: {len(result.events)} events from '{result.provider_id}'",
          "No NHL events from registry")

    # NHL should come from the specialized provider
    check(result.provider_id == "nhl",
          "NHL provider was used (specialized priority works)",
          f"Expected 'nhl' provider but got '{result.provider_id}'")

    if result.errors:
        for err in result.errors:
            warn(f"Provider warning: {err}")

    # NBA should come from general provider
    result_nba = await reg.get_events_with_status("nba")
    if result_nba.events:
        check(result_nba.provider_id != "nhl",
              f"NBA via registry: {len(result_nba.events)} events from '{result_nba.provider_id}'",
              "NHL provider incorrectly used for NBA")


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

async def main():
    global passed, failed, skipped

    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    print(f"{BOLD}Live Integration Check{RESET}")
    print("=" * 50)

    if target in ("all", "nhl"):
        await check_nhl()
    if target in ("all", "sportsdb"):
        await check_sportsdb()
    if target in ("all", "thetvapp"):
        await check_thetvapp()
    if target in ("all", "logos"):
        await check_logos()
    if target in ("all", "backend"):
        await check_backend()
    if target in ("all", "registry"):
        await check_registry_integration()

    print(f"\n{'=' * 50}")
    summary = f"{GREEN}{passed} passed{RESET}"
    if failed:
        summary += f", {RED}{failed} failed{RESET}"
    if skipped:
        summary += f", {YELLOW}{skipped} skipped{RESET}"
    print(f"{summary}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
