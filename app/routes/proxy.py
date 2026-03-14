import json
import os

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import Response, FileResponse

from app.services.extractor import StreamInfo
from app.services.hls_proxy import HLSProxyService

router = APIRouter()

# In-memory store of active proxy sessions: session_id -> StreamInfo
sessions: dict[str, StreamInfo] = {}


def _get_proxy_base(request: Request) -> str:
    from app.config import settings
    host = settings.get_public_host(request.url.port)
    return f"http://{host}/proxy"


def _build_headers(stream_info: StreamInfo) -> dict:
    headers = dict(stream_info.headers)
    if stream_info.cookies:
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in stream_info.cookies)
        headers["Cookie"] = cookie_str
    return headers


def _is_hls_playlist(content: str) -> bool:
    return content.strip().startswith("#EXTM3U")


@router.get("/playlist/{session_id}")
async def proxy_playlist(session_id: str, request: Request):
    session = sessions.get(session_id)
    if not session:
        return Response(status_code=404, content="Session not found")

    headers = _build_headers(session)

    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(session.m3u8_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return Response(status_code=502, content="Failed to fetch playlist")
                text = await resp.text()
    except Exception as e:
        print(f"[proxy] Playlist fetch error: {type(e).__name__}: {e}")
        return Response(status_code=502, content=f"Upstream error: {type(e).__name__}")

    proxy_base = _get_proxy_base(request)
    proxy_svc = HLSProxyService(proxy_base)

    rewritten = proxy_svc.rewrite_playlist(
        text, session.m3u8_url, session.headers,
    )

    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/segment")
async def proxy_segment(request: Request, url: str, h: str = ""):
    headers = json.loads(h) if h else {}

    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                body = await resp.read()
                upstream_ct = resp.headers.get("content-type", "video/mp2t")
    except Exception as e:
        print(f"[proxy] Segment fetch error: {type(e).__name__}: {e}")
        return Response(status_code=502, content=f"Upstream error: {type(e).__name__}")

    text = ""
    try:
        text = body.decode("utf-8", errors="ignore")
    except Exception:
        pass

    # If this is an HLS playlist (even without .m3u8 extension), rewrite it
    if _is_hls_playlist(text):
        proxy_base = _get_proxy_base(request)
        proxy_svc = HLSProxyService(proxy_base)
        rewritten = proxy_svc.rewrite_playlist(text, url, headers)
        print(f"[proxy] Rewrote playlist from {url[:80]}... ({len(text)} -> {len(rewritten)} bytes)")

        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # Otherwise it's a media segment — force correct content-type
    content_type = upstream_ct
    if "text/" in content_type or "octet-stream" in content_type:
        content_type = "video/mp2t"
    return Response(
        content=body,
        media_type=content_type,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/remux/{session_id}/{filename}")
async def serve_remux(session_id: str, filename: str, request: Request):
    """Serve ffmpeg-remuxed HLS files (clean .m3u8 + .ts segments)."""
    transcoder = request.app.state.transcoder
    output_dir = transcoder.get_output_dir(session_id)
    if not output_dir:
        return Response(status_code=404, content="Remux session not found")

    file_path = os.path.join(output_dir, filename)
    if not os.path.exists(file_path):
        return Response(status_code=404, content="File not found")

    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    else:
        media_type = "video/mp2t"

    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Access-Control-Allow-Origin": "*"},
    )
