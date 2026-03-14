import json
from urllib.parse import urlencode, urljoin

import m3u8


class HLSProxyService:
    def __init__(self, proxy_base_url: str):
        self.proxy_base_url = proxy_base_url

    def rewrite_playlist(self, playlist_text: str, base_url: str,
                         original_headers: dict) -> str:
        parsed = m3u8.loads(playlist_text)

        for segment in parsed.segments:
            segment.uri = self._make_proxy_url(
                self._resolve_url(segment.uri, base_url),
                original_headers,
            )

        for playlist in parsed.playlists:
            playlist.uri = self._make_proxy_url(
                self._resolve_url(playlist.uri, base_url),
                original_headers,
            )

        for key in parsed.keys:
            if key and key.uri:
                key.uri = self._make_proxy_url(
                    self._resolve_url(key.uri, base_url),
                    original_headers,
                )

        return parsed.dumps()

    def _make_proxy_url(self, original_url: str, headers: dict) -> str:
        params = {"url": original_url}
        if headers:
            params["h"] = json.dumps(headers)
        return f"{self.proxy_base_url}/segment?{urlencode(params)}"

    def _resolve_url(self, uri: str, base_url: str) -> str:
        if uri.startswith(("http://", "https://")):
            return uri
        return urljoin(base_url, uri)
