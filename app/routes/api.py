import asyncio
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import settings
from app.services.airplay import AirPlayService
from app.routes.proxy import sessions

router = APIRouter()
airplay_service = AirPlayService(settings.CREDENTIAL_FILE)


class ExtractRequest(BaseModel):
    url: str


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
    try:
        scraper = request.app.state.scraper
        events = await scraper.scrape_category(category)
        return {"events": [ev.model_dump() for ev in events]}
    except Exception as e:
        return {"error": f"Failed to fetch category {category}: {str(e)}"}


@router.post("/extract")
async def extract_stream(body: ExtractRequest, request: Request):
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


@router.post("/cast")
async def cast_to_device(body: CastRequest, request: Request):
    stream_info = sessions.get(body.session_id)
    if not stream_info:
        return {"error": "Session not found"}

    public_host = settings.get_public_host(request.url.port)

    # Start ffmpeg reading the ORIGINAL m3u8 directly from the CDN
    # (bypasses our proxy URLs which confuse ffmpeg's HLS extension parser)
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

    # Send the remuxed URL to Apple TV (uses LAN IP so Apple TV can reach it)
    remux_url = f"http://{public_host}/proxy/remux/{body.session_id}/stream.m3u8"
    print(f"[cast] Sending remuxed URL to Apple TV: {remux_url}")

    # Run cast in background — play_url now keeps the timing server alive
    # for the duration of playback (blocks for hours), so we can't await it
    async def _do_cast():
        try:
            await airplay_service.cast(body.device_id, remux_url)
        except Exception as e:
            print(f"[cast] AirPlay error: {e}")

    asyncio.create_task(_do_cast())

    # Give pyatv a moment to send the play command before returning
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
        # Need to pair another protocol — auto-start it
        try:
            await airplay_service.start_pairing(body.device_id)
            return {"status": "more_pairing", "message": "First protocol paired! Enter the NEW PIN shown on your TV for the second pairing."}
        except Exception as e:
            return {"status": "paired_partial", "message": f"Partially paired. Cast may still work. ({e})"}
    if result:
        return {"status": "paired"}
    return {"error": "Pairing failed. Check the PIN and try again."}
