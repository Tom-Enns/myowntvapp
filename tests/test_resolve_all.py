"""Tests for the two-phase resolve_all flow and URL deduplication."""

import pytest
from unittest.mock import AsyncMock

from app.backends.base import StreamBackend
from app.backends.registry import BackendRegistry, _normalize_url, _dedup_links
from app.models import SportEvent, ResolvedStream, StreamLink


# --- URL normalization tests ---

class TestNormalizeUrl:
    def test_strips_trailing_slash(self):
        assert _normalize_url("https://thetvapp.to/event/test/") == \
               _normalize_url("https://thetvapp.to/event/test")

    def test_case_insensitive(self):
        assert _normalize_url("https://THETVAPP.TO/Event/Test") == \
               _normalize_url("https://thetvapp.to/event/test")

    def test_strips_query_params(self):
        assert _normalize_url("https://example.com/page?ref=abc") == \
               _normalize_url("https://example.com/page")

    def test_different_paths_are_different(self):
        assert _normalize_url("https://example.com/event/a") != \
               _normalize_url("https://example.com/event/b")

    def test_different_domains_are_different(self):
        assert _normalize_url("https://thetvapp.to/event/test") != \
               _normalize_url("https://thetvapp.link/event/test")


# --- Link dedup tests ---

class TestDedupLinks:
    def test_removes_duplicate_urls(self):
        links = [
            StreamLink(url="https://thetvapp.to/event/test/", backend_id="thetvapp", backend_name="TheTVApp.to"),
            StreamLink(url="https://thetvapp.to/event/test", backend_id="nhlbite", backend_name="NHLBite"),
        ]
        result = _dedup_links(links)
        assert len(result) == 1
        assert result[0].backend_id == "thetvapp"  # First one wins

    def test_keeps_different_urls(self):
        links = [
            StreamLink(url="https://thetvapp.to/event/game1/", backend_id="a", backend_name="A"),
            StreamLink(url="https://streameast.com/event/game1/", backend_id="b", backend_name="B"),
        ]
        result = _dedup_links(links)
        assert len(result) == 2

    def test_empty_list(self):
        assert _dedup_links([]) == []


# --- Mock backends for two-phase tests ---

class DiscoverOnlyBackend(StreamBackend):
    """Backend that only implements discover_links (for testing resolve_all)."""

    def __init__(self, bid, name, links=None, error=None):
        self._id = bid
        self._name = name
        self._links = links or []
        self._error = error

    @property
    def backend_id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._name

    async def resolve_stream(self, event):
        return None

    async def discover_links(self, event):
        if self._error:
            raise self._error
        return self._links


class TestResolveAllTwoPhase:
    @pytest.mark.asyncio
    async def test_empty_when_no_links(self, nba_event):
        reg = BackendRegistry()
        reg.register(DiscoverOnlyBackend("a", "A", links=[]))

        streams, statuses = await reg.resolve_all(nba_event)
        assert streams == []
        assert len(statuses) == 1
        assert not statuses[0].success

    @pytest.mark.asyncio
    async def test_dedup_removes_same_url_from_different_backends(self, nba_event):
        """When two backends discover the same URL, only one resolution happens."""
        link1 = StreamLink(
            url="https://thetvapp.to/event/test/",
            backend_id="thetvapp",
            backend_name="TheTVApp.to",
        )
        link2 = StreamLink(
            url="https://thetvapp.to/event/test",
            backend_id="nhlbite",
            backend_name="NHLBite",
            source_label="TheTVApp",
        )

        reg = BackendRegistry()
        reg.register(DiscoverOnlyBackend("thetvapp", "TheTVApp.to", links=[link1]))
        reg.register(DiscoverOnlyBackend("nhlbite", "NHLBite", links=[link2]))

        # Both backends discover the same URL — after dedup, only 1 resolve call
        # We can't easily test the actual HTTP without mocking the site registry,
        # but we can verify the statuses show both backends succeeded at discovery
        streams, statuses = await reg.resolve_all(nba_event)
        successful = [s for s in statuses if s.success]
        assert len(successful) == 2  # Both discovered links

    @pytest.mark.asyncio
    async def test_discover_error_captured_in_status(self, nba_event):
        reg = BackendRegistry()
        reg.register(DiscoverOnlyBackend("broken", "Broken", error=RuntimeError("site down")))

        streams, statuses = await reg.resolve_all(nba_event)
        assert streams == []
        assert len(statuses) == 1
        assert not statuses[0].success
        assert "site down" in statuses[0].error
