"""Tests for the backend registry."""

import pytest
from unittest.mock import AsyncMock

from app.backends.base import StreamBackend
from app.backends.registry import BackendRegistry
from app.models import SportEvent, ResolvedStream


class MockBackend(StreamBackend):
    """A mock backend for testing registry behavior."""

    def __init__(self, bid: str, name: str, stream=None, error=None):
        self._id = bid
        self._name = name
        self._stream = stream
        self._error = error

    @property
    def backend_id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._name

    async def resolve_stream(self, event: SportEvent) -> ResolvedStream | None:
        if self._error:
            raise self._error
        return self._stream


def _make_stream(backend_id="mock", name="Mock"):
    return ResolvedStream(
        backend_id=backend_id,
        backend_name=name,
        m3u8_url="https://example.com/stream.m3u8",
    )


class TestBackendRegistry:
    def test_register_and_list(self):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "Backend A"))
        reg.register(MockBackend("b", "Backend B"))

        backends = reg.list_backends()
        assert len(backends) == 2
        assert backends[0]["id"] == "a"
        assert backends[1]["id"] == "b"

    def test_priority_ordering(self):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "A"))
        reg.register(MockBackend("b", "B"))
        reg.register(MockBackend("c", "C"))

        # Default order is registration order
        assert reg.get_priority() == ["a", "b", "c"]

        # Reorder
        reg.set_priority(["c", "a"])
        assert reg.get_priority() == ["c", "a", "b"]

    def test_get_backend(self):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "A"))
        assert reg.get_backend("a") is not None
        assert reg.get_backend("nonexistent") is None

    @pytest.mark.asyncio
    async def test_resolve_best_returns_first_success(self, nba_event):
        reg = BackendRegistry()
        reg.register(MockBackend("slow", "Slow", stream=None))
        reg.register(MockBackend("fast", "Fast", stream=_make_stream("fast", "Fast")))

        stream, attempts = await reg.resolve_best(nba_event)

        assert stream is not None
        assert stream.backend_id == "fast"
        assert len(attempts) == 2
        assert not attempts[0].success  # slow returned None
        assert attempts[1].success      # fast returned stream

    @pytest.mark.asyncio
    async def test_resolve_best_skips_errors(self, nba_event):
        reg = BackendRegistry()
        reg.register(MockBackend("broken", "Broken", error=PermissionError("403 blocked")))
        reg.register(MockBackend("good", "Good", stream=_make_stream("good", "Good")))

        stream, attempts = await reg.resolve_best(nba_event)

        assert stream is not None
        assert stream.backend_id == "good"
        assert len(attempts) == 2
        assert not attempts[0].success
        assert "403 blocked" in attempts[0].error
        assert attempts[1].success

    @pytest.mark.asyncio
    async def test_resolve_best_all_fail(self, nba_event):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "A", error=RuntimeError("down")))
        reg.register(MockBackend("b", "B", stream=None))

        stream, attempts = await reg.resolve_best(nba_event)

        assert stream is None
        assert len(attempts) == 2
        assert not attempts[0].success
        assert "down" in attempts[0].error
        assert not attempts[1].success
        assert "No stream found" in attempts[1].error

    @pytest.mark.asyncio
    async def test_resolve_best_empty_registry(self, nba_event):
        reg = BackendRegistry()
        stream, attempts = await reg.resolve_best(nba_event)
        assert stream is None
        assert attempts == []

    @pytest.mark.asyncio
    async def test_resolve_all(self, nba_event):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "A", stream=_make_stream("a", "A")))
        reg.register(MockBackend("b", "B", error=RuntimeError("oops")))

        results = await reg.resolve(nba_event)

        assert len(results) == 2
        assert results[0].success
        assert not results[1].success
        assert results[1].error == "oops"

    @pytest.mark.asyncio
    async def test_resolve_best_records_latency(self, nba_event):
        reg = BackendRegistry()
        reg.register(MockBackend("a", "A", stream=_make_stream()))

        stream, attempts = await reg.resolve_best(nba_event)

        assert attempts[0].latency_ms >= 0
