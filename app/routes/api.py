import asyncio
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import settings
from app.models import SportEvent
from app.services.airplay import AirPlayService
from app.routes.proxy import sessions
from app.services.extractor import StreamInfo


def _stream_source_name(m3u8_url: str) -> str:
    """Extract a human-friendly source name from an m3u8 URL's domain.

    e.g. 'https://lpnba.akamaized.net/.../index.m3u8' → 'akamaized.net'
         'https://chevy.soyspace.cyou/proxy/...'       → 'soyspace.cyou'
         'https://streambtw.com/live/...'               → 'streambtw.com'
    """
    try:
        host = urlparse(m3u8_url).hostname or ""
    except Exception:
        return "unknown"
    parts = host.split(".")
    # For 3+ part domains, drop the subdomain to get the recognisable base
    # e.g. lpnba.akamaized.net → akamaized.net, fg1.fgfbg5433dd.site → fgfbg5433dd.site
    if len(parts) > 2:
        return ".".join(parts[-2:])
    return host or "unknown"

router = APIRouter()
airplay_service = AirPlayService(settings.CREDENTIAL_FILE)


class ExtractRequest(BaseModel):
    url: str


class ResolveRequest(BaseModel):
    """New: resolve a stream for an event using the backend registry."""
    event_id: str
    title: str
    category: str
    home_team: str | None = None
    away_team: str | None = None
    home_logo: str | None = None
    away_logo: str | None = None
    # Optional: force a specific backend
    backend_id: str | None = None


class CastRequest(BaseModel):
    device_id: str
    session_id: str


class PairFinishRequest(BaseModel):
    device_id: str
    pin: int


@router.get("/devices")
async def list_devices():
    devices = await airplay_service.discover()
    return {"devices": [{"name": d.name, "identifier": d.identifier, "address": d.address} for d in devices]}


@router.get("/sports/{category}")
async def list_sports_category(category: str, request: Request):
    """Fetch events from the schedule registry (backend-agnostic)."""
    try:
        schedule_registry = request.app.state.schedule_registry
        result = await schedule_registry.get_events_with_status(category)
        response = {"events": [ev.model_dump(mode="json") for ev in result.events]}
        if result.errors:
            response["warnings"] = result.errors
        if result.provider_id:
            response["provider"] = result.provider_id
        return response
    except Exception as e:
        return {"error": f"Failed to fetch category {category}: {str(e)}"}


@router.post("/resolve")
async def resolve_stream(body: ResolveRequest, request: Request):
    """Resolve a stream for an event using the backend registry with fallback."""
    backend_registry = request.app.state.backend_registry

    event = SportEvent(
        event_id=body.event_id,
        title=body.title,
        category=body.category,
        home_team=body.home_team,
        away_team=body.away_team,
        home_logo=body.home_logo,
        away_logo=body.away_logo,
    )

    if body.backend_id:
        # Try a specific backend
        backend = backend_registry.get_backend(body.backend_id)
        if not backend:
            return {"error": f"Unknown backend: {body.backend_id}"}
        try:
            stream = await backend.resolve_stream(event)
            if not stream:
                return {"error": f"Backend {backend.display_name} found no stream"}
        except Exception as e:
            return {"error": f"Backend {backend.display_name} failed: {str(e)}"}
    else:
        # Try all backends in priority order
        stream, attempts = await backend_registry.resolve_best(event)
        if not stream:
            # Build a detailed error from all backend attempts
            error_details = []
            for attempt in attempts:
                error_details.append(f"{attempt.backend_name}: {attempt.error}")
            summary = "; ".join(error_details) if error_details else "No backends available"
            return {
                "error": f"All backends failed to resolve a stream. {summary}",
                "backend_errors": [
                    {"backend": a.backend_name, "error": a.error, "latency_ms": a.latency_ms}
                    for a in attempts
                ],
            }

    session_id = str(uuid.uuid4())
    sessions[session_id] = StreamInfo(
        m3u8_url=stream.m3u8_url,
        headers=stream.headers,
        cookies=stream.cookies,
    )

    public_host = settings.get_public_host(request.url.port)
    proxy_url = f"http://{public_host}/proxy/playlist/{session_id}"

    return {
        "session_id": session_id,
        "proxy_url": proxy_url,
        "original_m3u8": stream.m3u8_url,
        "backend_id": stream.backend_id,
        "backend_name": stream.backend_name,
        "stream_source": _stream_source_name(stream.m3u8_url),
        "qualities": [q.model_dump(mode="json") for q in stream.qualities],
    }


@router.post("/resolve-all")
async def resolve_all_streams(body: ResolveRequest, request: Request):
    """Resolve ALL available streams from ALL backends in parallel.

    Returns multiple streams (deduped, sorted by quality) so the
    frontend can offer a stream picker. Auto-plays the best one.
    """
    backend_registry = request.app.state.backend_registry

    event = SportEvent(
        event_id=body.event_id,
        title=body.title,
        category=body.category,
        home_team=body.home_team,
        away_team=body.away_team,
        home_logo=body.home_logo,
        away_logo=body.away_logo,
    )

    streams, statuses = await backend_registry.resolve_all(event)

    if not streams:
        error_details = [f"{s.backend_name}: {s.error}" for s in statuses if not s.success]
        summary = "; ".join(error_details) if error_details else "No backends available"
        return {
            "error": f"No streams found. {summary}",
            "backend_errors": [
                {"backend": s.backend_name, "error": s.error, "latency_ms": s.latency_ms}
                for s in statuses if not s.success
            ],
        }

    # Create proxy sessions for each stream
    results = []
    public_host = settings.get_public_host(request.url.port)
    for stream in streams:
        session_id = str(uuid.uuid4())
        sessions[session_id] = StreamInfo(
            m3u8_url=stream.m3u8_url,
            headers=stream.headers,
            cookies=stream.cookies,
        )
        results.append({
            "session_id": session_id,
            "proxy_url": f"http://{public_host}/proxy/playlist/{session_id}",
            "original_m3u8": stream.m3u8_url,
            "backend_id": stream.backend_id,
            "backend_name": stream.backend_name,
            "stream_source": _stream_source_name(stream.m3u8_url),
            "source_label": stream.source_label,
            "qualities": [q.model_dump(mode="json") for q in stream.qualities],
        })

    return {
        "streams": results,
        "backend_statuses": [
            {
                "backend": s.backend_name,
                "success": s.success,
                "error": s.error,
                "latency_ms": s.latency_ms,
            }
            for s in statuses
        ],
    }


@router.post("/extract")
async def extract_stream(body: ExtractRequest, request: Request):
    """Legacy extract endpoint — still works for direct URL extraction."""
    extractor = request.app.state.extractor
    try:
        stream_info = await extractor.extract(body.url, timeout_s=settings.EXTRACT_TIMEOUT_S)
    except Exception as e:
        msg = str(e) or type(e).__name__
        print(f"[extract] Error: {type(e).__name__}: {e}")
        return {"error": f"Stream extraction failed: {msg}"}

    session_id = str(uuid.uuid4())
    sessions[session_id] = stream_info

    public_host = settings.get_public_host(request.url.port)
    proxy_url = f"http://{public_host}/proxy/playlist/{session_id}"

    return {
        "session_id": session_id,
        "proxy_url": proxy_url,
        "original_m3u8": stream_info.m3u8_url,
    }


# ---- Backend Management ----

@router.get("/backends")
async def list_backends(request: Request):
    """List registered stream backends in priority order."""
    registry = request.app.state.backend_registry
    return {"backends": registry.list_backends(), "priority": registry.get_priority()}


@router.put("/backends/priority")
async def set_backend_priority(body: dict, request: Request):
    """Set backend priority order. Body: {"priority": ["thetvapp", "other"]}"""
    registry = request.app.state.backend_registry
    priority = body.get("priority", [])
    registry.set_priority(priority)
    return {"priority": registry.get_priority()}


@router.get("/backends/{backend_id}/health")
async def backend_health(backend_id: str, request: Request):
    """Check if a specific backend is reachable."""
    registry = request.app.state.backend_registry
    backend = registry.get_backend(backend_id)
    if not backend:
        return {"error": "Unknown backend"}
    healthy = await backend.health_check()
    return {"backend_id": backend_id, "healthy": healthy}


# ---- Schedule Provider Management ----

@router.get("/schedule/providers")
async def list_schedule_providers(request: Request):
    """List registered schedule providers."""
    registry = request.app.state.schedule_registry
    return {"providers": registry.list_providers()}


# ---- Casting (unchanged) ----

@router.post("/cast")
async def cast_to_device(body: CastRequest, request: Request):
    stream_info = sessions.get(body.session_id)
    if not stream_info:
        return {"error": "Session not found"}

    public_host = settings.get_public_host(request.url.port)

    transcoder = request.app.state.transcoder
    try:
        print(f"[cast] Starting ffmpeg remux for session {body.session_id[:8]}...")
        await transcoder.start_remux(
            body.session_id,
            m3u8_url=stream_info.m3u8_url,
            headers=stream_info.headers,
            cookies=stream_info.cookies,
        )
    except Exception as e:
        print(f"[cast] Remux failed: {e}")
        return {"error": f"Failed to prepare stream for Apple TV: {e}"}

    remux_url = f"http://{public_host}/proxy/remux/{body.session_id}/stream.m3u8"
    print(f"[cast] Sending remuxed URL to Apple TV: {remux_url}")

    async def _do_cast():
        try:
            await airplay_service.cast(body.device_id, remux_url)
        except Exception as e:
            print(f"[cast] AirPlay error: {e}")

    asyncio.create_task(_do_cast())
    await asyncio.sleep(3)

    return {"status": "casting", "url": remux_url}


@router.post("/prepare-remux")
async def prepare_remux(body: dict, request: Request):
    """Prepare remuxed stream without casting — for testing."""
    session_id = body.get("session_id", "")
    stream_info = sessions.get(session_id)
    if not stream_info:
        return {"error": "Session not found"}

    public_host = settings.get_public_host(request.url.port)
    transcoder = request.app.state.transcoder
    try:
        await transcoder.start_remux(
            session_id,
            m3u8_url=stream_info.m3u8_url,
            headers=stream_info.headers,
            cookies=stream_info.cookies,
        )
    except Exception as e:
        return {"error": f"Remux failed: {e}"}

    remux_url = f"http://{public_host}/proxy/remux/{session_id}/stream.m3u8"
    return {"status": "ready", "remux_url": remux_url}


@router.post("/pair/start")
async def start_pairing(body: dict):
    device_id = body.get("device_id", "")
    try:
        await airplay_service.start_pairing(device_id)
    except Exception as e:
        return {"error": str(e)}
    return {"status": "pin_required", "message": "Enter the PIN shown on your TV"}


@router.post("/pair/finish")
async def finish_pairing(body: PairFinishRequest):
    try:
        result = await airplay_service.finish_pairing(body.device_id, body.pin)
    except Exception as e:
        return {"error": str(e)}

    if result == "more":
        try:
            await airplay_service.start_pairing(body.device_id)
            return {"status": "more_pairing", "message": "First protocol paired! Enter the NEW PIN shown on your TV for the second pairing."}
        except Exception as e:
            return {"status": "paired_partial", "message": f"Partially paired. Cast may still work. ({e})"}
    if result:
        return {"status": "paired"}
    return {"error": "Pairing failed. Check the PIN and try again."}
