"""Tests for API routes using FastAPI TestClient.

Uses mocked registries so no real HTTP calls are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from fastapi import FastAPI

from app.models import SportEvent, ResolvedStream, StreamQuality, BackendStatus
from app.routes.api import router
from app.schedule.registry import ScheduleResult


def create_test_app(schedule_result=None, resolve_result=None, backend_list=None):
    """Create a FastAPI app with mocked registries."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # Mock schedule registry
    schedule_reg = MagicMock()
    if schedule_result is not None:
        schedule_reg.get_events_with_status = AsyncMock(return_value=schedule_result)
    else:
        schedule_reg.get_events_with_status = AsyncMock(return_value=ScheduleResult())

    # Mock backend registry
    backend_reg = MagicMock()
    if resolve_result is not None:
        backend_reg.resolve_best = AsyncMock(return_value=resolve_result)
    else:
        backend_reg.resolve_best = AsyncMock(return_value=(None, []))
    backend_reg.get_backend = MagicMock(return_value=None)
    backend_reg.list_backends = MagicMock(return_value=backend_list or [])

    app.state.schedule_registry = schedule_reg
    app.state.backend_registry = backend_reg
    # Stub other state the routes might access
    app.state.extractor = MagicMock()
    app.state.transcoder = MagicMock()

    return app


def _make_events(n=2):
    return [
        SportEvent(event_id=f"test:{i}", title=f"Game {i}", category="nba")
        for i in range(n)
    ]


class TestListSportsCategory:
    @pytest.mark.asyncio
    async def test_returns_events(self):
        events = _make_events(3)
        result = ScheduleResult(events=events, provider_id="thetvapp")
        app = create_test_app(schedule_result=result)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/sports/nba")

        data = resp.json()
        assert len(data["events"]) == 3
        assert data["provider"] == "thetvapp"
        assert "warnings" not in data

    @pytest.mark.asyncio
    async def test_returns_warnings_when_provider_failed(self):
        """When primary provider fails but fallback succeeds."""
        events = _make_events(1)
        result = ScheduleResult(
            events=events,
            errors=["TheTVApp.to: 403 Forbidden. Your IP may be blocked."],
            provider_id="sportsdb",
        )
        app = create_test_app(schedule_result=result)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/sports/nba")

        data = resp.json()
        assert len(data["events"]) == 1
        assert len(data["warnings"]) == 1
        assert "403" in data["warnings"][0]
        assert data["provider"] == "sportsdb"

    @pytest.mark.asyncio
    async def test_empty_events_with_errors(self):
        """All providers failed — errors should be surfaced."""
        result = ScheduleResult(
            events=[],
            errors=[
                "TheTVApp.to: 403 Forbidden. Your IP may be blocked.",
                "TheSportsDB: No events found for category nba",
            ],
        )
        app = create_test_app(schedule_result=result)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/sports/nba")

        data = resp.json()
        assert data["events"] == []
        assert len(data["warnings"]) == 2

    @pytest.mark.asyncio
    async def test_empty_events_no_errors(self):
        app = create_test_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/sports/nba")

        data = resp.json()
        assert data["events"] == []
        assert "warnings" not in data


class TestResolveStream:
    @pytest.mark.asyncio
    async def test_successful_resolution(self):
        stream = ResolvedStream(
            backend_id="thetvapp",
            backend_name="TheTVApp.to",
            m3u8_url="https://stream.example.com/master.m3u8",
            qualities=[StreamQuality(resolution="1080p", bandwidth=6000000)],
        )
        attempts = [BackendStatus(
            backend_id="thetvapp",
            backend_name="TheTVApp.to",
            success=True,
            stream=stream,
            latency_ms=200,
        )]
        app = create_test_app(resolve_result=(stream, attempts))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/resolve", json={
                "event_id": "thetvapp:test",
                "title": "Test Game",
                "category": "nba",
            })

        data = resp.json()
        assert "error" not in data
        assert data["backend_id"] == "thetvapp"
        assert data["session_id"]
        assert data["proxy_url"]

    @pytest.mark.asyncio
    async def test_all_backends_fail_returns_details(self):
        attempts = [
            BackendStatus(
                backend_id="thetvapp",
                backend_name="TheTVApp.to",
                success=False,
                error="TheTVApp.to returned 403 Forbidden. Your IP address appears to be blocked.",
                latency_ms=120,
            ),
        ]
        app = create_test_app(resolve_result=(None, attempts))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/resolve", json={
                "event_id": "thetvapp:test",
                "title": "Test Game",
                "category": "nba",
            })

        data = resp.json()
        assert "error" in data
        assert "All backends failed" in data["error"]
        assert "backend_errors" in data
        assert len(data["backend_errors"]) == 1
        assert "403" in data["backend_errors"][0]["error"]
        assert data["backend_errors"][0]["latency_ms"] == 120

    @pytest.mark.asyncio
    async def test_unknown_backend(self):
        app = create_test_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/resolve", json={
                "event_id": "test:1",
                "title": "Test",
                "category": "nba",
                "backend_id": "nonexistent",
            })

        data = resp.json()
        assert "Unknown backend" in data["error"]
