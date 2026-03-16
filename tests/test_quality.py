"""Tests for stream quality parsing."""

import pytest
from aioresponses import aioresponses

from app.services.quality import parse_stream_qualities
from tests.conftest import load_fixture


class TestParseStreamQualities:
    @pytest.mark.asyncio
    async def test_master_playlist(self):
        m3u8_text = load_fixture("master_playlist.m3u8")
        url = "https://stream.example.com/master.m3u8"

        with aioresponses() as m:
            m.get(url, body=m3u8_text)
            qualities = await parse_stream_qualities(url, {})

        assert len(qualities) == 3
        # Should be sorted by bandwidth descending
        assert qualities[0].resolution == "1080p"
        assert qualities[0].bandwidth == 6000000
        assert qualities[0].width == 1920
        assert qualities[0].height == 1080
        assert qualities[1].resolution == "720p"
        assert qualities[2].resolution == "480p"

    @pytest.mark.asyncio
    async def test_media_playlist_returns_empty(self):
        m3u8_text = load_fixture("media_playlist.m3u8")
        url = "https://stream.example.com/720p/playlist.m3u8"

        with aioresponses() as m:
            m.get(url, body=m3u8_text)
            qualities = await parse_stream_qualities(url, {})

        assert qualities == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        url = "https://stream.example.com/master.m3u8"

        with aioresponses() as m:
            m.get(url, status=403)
            qualities = await parse_stream_qualities(url, {})

        assert qualities == []

    @pytest.mark.asyncio
    async def test_passes_headers(self):
        m3u8_text = load_fixture("master_playlist.m3u8")
        url = "https://stream.example.com/master.m3u8"
        headers = {"Referer": "https://thetvapp.to/"}

        with aioresponses() as m:
            m.get(url, body=m3u8_text)
            qualities = await parse_stream_qualities(url, headers)

        assert len(qualities) == 3
