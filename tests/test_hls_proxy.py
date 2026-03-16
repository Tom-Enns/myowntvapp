"""Tests for HLS proxy playlist rewriting."""

import json
from urllib.parse import parse_qs, urlparse

from app.services.hls_proxy import HLSProxyService
from tests.conftest import load_fixture


class TestHLSProxyService:
    def setup_method(self):
        self.proxy = HLSProxyService("http://localhost:1919/proxy")

    def test_rewrite_master_playlist(self):
        playlist = load_fixture("master_playlist.m3u8")
        base_url = "https://stream.example.com/"

        result = self.proxy.rewrite_playlist(playlist, base_url, {"Referer": "https://thetvapp.to/"})

        # All variant URIs should be proxied
        for line in result.splitlines():
            if line and not line.startswith("#"):
                assert line.startswith("http://localhost:1919/proxy/segment?")
                parsed = urlparse(line)
                params = parse_qs(parsed.query)
                assert "url" in params
                assert "h" in params
                headers = json.loads(params["h"][0])
                assert headers["Referer"] == "https://thetvapp.to/"

    def test_rewrite_media_playlist(self):
        playlist = load_fixture("media_playlist.m3u8")
        base_url = "https://stream.example.com/720p/"

        result = self.proxy.rewrite_playlist(playlist, base_url, {})

        segments = [l for l in result.splitlines() if l and not l.startswith("#")]
        assert len(segments) == 4
        # Relative segments should be resolved to absolute URLs
        for seg in segments:
            parsed = urlparse(seg)
            params = parse_qs(parsed.query)
            url = params["url"][0]
            assert url.startswith("https://stream.example.com/720p/segment_")

    def test_rewrite_encrypted_key(self):
        playlist = load_fixture("media_playlist.m3u8")
        base_url = "https://stream.example.com/720p/"

        result = self.proxy.rewrite_playlist(playlist, base_url, {})

        # The encryption key URI should be rewritten
        assert "keys.example.com/key.bin" not in result
        assert "proxy/segment" in result

    def test_resolve_absolute_url(self):
        url = self.proxy._resolve_url("https://cdn.example.com/seg.ts", "https://base.example.com/")
        assert url == "https://cdn.example.com/seg.ts"

    def test_resolve_relative_url(self):
        url = self.proxy._resolve_url("seg.ts", "https://cdn.example.com/hls/")
        assert url == "https://cdn.example.com/hls/seg.ts"
