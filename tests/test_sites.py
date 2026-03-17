"""Tests for the site layer — site handlers and site registry."""

import pytest
from aioresponses import aioresponses
from base64 import b64encode

from app.sites.registry import SiteRegistry, get_site_registry
from app.sites.generic import GenericSite, find_iframe_url
from app.sites.thetvapp import TheTVAppSite
from tests.conftest import load_fixture


class TestFindIframeUrl:
    """Test the shared iframe-finding utility."""

    def test_finds_cx_iframe(self):
        html = '<html><body><iframe id="cx-iframe" src="https://embed.example.com/stream"></iframe></body></html>'
        assert find_iframe_url(html, "https://example.com/") == "https://embed.example.com/stream"

    def test_finds_embed_iframe(self):
        html = '<html><body><iframe src="https://player.example.com/embed/123"></iframe></body></html>'
        assert find_iframe_url(html, "https://example.com/") == "https://player.example.com/embed/123"

    def test_finds_stream_iframe(self):
        html = '<html><body><iframe src="https://player.example.com/stream/abc"></iframe></body></html>'
        assert find_iframe_url(html, "https://example.com/") == "https://player.example.com/stream/abc"

    def test_falls_back_to_any_http_iframe(self):
        html = '<html><body><iframe src="https://cdn.example.com/player"></iframe></body></html>'
        assert find_iframe_url(html, "https://example.com/") == "https://cdn.example.com/player"

    def test_skips_about_blank(self):
        html = '<html><body><iframe src="about:blank"></iframe></body></html>'
        assert find_iframe_url(html, "https://example.com/") is None

    def test_no_iframe_returns_none(self):
        html = '<html><body>No iframe here</body></html>'
        assert find_iframe_url(html, "https://example.com/") is None

    def test_finds_js_injected_cx_iframe(self):
        html = """<html><body><script>
        document.getElementById('cx-iframe').src = 'https://embed.example.com/live';
        </script></body></html>"""
        assert find_iframe_url(html, "https://example.com/") == "https://embed.example.com/live"

    def test_from_fixture(self):
        html = load_fixture("thetvapp_event_page_iframe.html")
        url = find_iframe_url(html, "https://thetvapp.to/event/test/")
        assert url == "https://embed.example.com/embed/stream123"


class TestSiteRegistry:
    def test_registers_sites_by_domain(self):
        registry = SiteRegistry()
        site = TheTVAppSite()
        registry.register(site)

        assert registry.get_site("https://thetvapp.to/event/test").site_id == "thetvapp"
        assert registry.get_site("https://thetvapp.link/nbastreams/game").site_id == "thetvapp"

    def test_falls_back_to_generic(self):
        registry = SiteRegistry()
        registry.register(GenericSite())

        assert registry.get_site("https://unknown-site.com/page").site_id == "generic"

    def test_matches_subdomain(self):
        registry = SiteRegistry()
        site = TheTVAppSite()
        registry.register(site)

        assert registry.get_site("https://www.thetvapp.to/event/test").site_id == "thetvapp"

    def test_singleton_has_thetvapp_registered(self):
        registry = get_site_registry()
        sites = registry.list_sites()
        site_ids = [s["id"] for s in sites]
        assert "thetvapp" in site_ids
        assert "generic" in site_ids


class TestGenericSite:
    @pytest.mark.asyncio
    async def test_resolves_iframe_page(self):
        """Generic site: page with iframe → resolver extracts m3u8."""
        event_page = '<html><body><iframe src="https://embed.example.com/embed/abc"></iframe></body></html>'
        encoded = b64encode(b"https://stream.example.com/live.m3u8").decode()
        iframe_html = f"<html><script>var url = atob('{encoded}');</script></html>"

        import aiohttp
        site = GenericSite()

        with aioresponses() as m:
            m.get("https://some-streaming-site.com/watch", body=event_page)
            m.get("https://embed.example.com/embed/abc", body=iframe_html)

            async with aiohttp.ClientSession() as session:
                result = await site.resolve(
                    "https://some-streaming-site.com/watch", None, session
                )

        assert result is not None
        assert result.m3u8_url == "https://stream.example.com/live.m3u8"

    @pytest.mark.asyncio
    async def test_resolves_direct_m3u8_in_page(self):
        """Generic site: no iframe, but m3u8 URL directly in page JS."""
        page_html = """<html><script>
        source: 'https://cdn.example.com/stream.m3u8'
        </script></html>"""

        import aiohttp
        site = GenericSite()

        with aioresponses() as m:
            m.get("https://some-site.com/watch", body=page_html)

            async with aiohttp.ClientSession() as session:
                result = await site.resolve("https://some-site.com/watch", None, session)

        assert result is not None
        assert result.m3u8_url == "https://cdn.example.com/stream.m3u8"

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        import aiohttp
        site = GenericSite()

        with aioresponses() as m:
            m.get("https://some-site.com/watch", status=404)

            async with aiohttp.ClientSession() as session:
                result = await site.resolve("https://some-site.com/watch", None, session)

        assert result is None


class TestTheTVAppSite:
    @pytest.mark.asyncio
    async def test_resolves_tv_channel(self):
        """TheTVApp site: TV channel page → token endpoint → m3u8."""
        page_html = load_fixture("thetvapp_tv_channel_page.html")

        import aiohttp
        site = TheTVAppSite()

        with aioresponses() as m:
            m.get("https://thetvapp.to/tv/espn/", body=page_html)
            m.get(
                "https://thetvapp.to/token/espn_stream_001",
                payload={"url": "https://stream.example.com/espn/master.m3u8"},
            )

            async with aiohttp.ClientSession() as session:
                result = await site.resolve("https://thetvapp.to/tv/espn/", None, session)

        assert result is not None
        assert result.m3u8_url == "https://stream.example.com/espn/master.m3u8"

    @pytest.mark.asyncio
    async def test_resolves_iframe_event(self):
        """TheTVApp site: sports event page → iframe → resolver → m3u8."""
        event_page = load_fixture("thetvapp_event_page_iframe.html")
        iframe_html = load_fixture("iframe_with_atob_stream.html")

        import aiohttp
        site = TheTVAppSite()

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", body=event_page)
            m.get("https://embed.example.com/embed/stream123", body=iframe_html)

            async with aiohttp.ClientSession() as session:
                result = await site.resolve("https://thetvapp.to/event/test/", None, session)

        assert result is not None
        assert result.m3u8_url == "https://stream.example.com/live/master.m3u8"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        import aiohttp
        site = TheTVAppSite()

        with aioresponses() as m:
            m.get("https://thetvapp.to/event/test/", status=403)

            async with aiohttp.ClientSession() as session:
                result = await site.resolve("https://thetvapp.to/event/test/", None, session)

        assert result is None
