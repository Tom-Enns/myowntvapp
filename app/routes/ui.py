import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/watch/{session_id}", response_class=HTMLResponse)
async def watch(session_id: str, request: Request):
    """Safari-friendly page with native HLS video + AirPlay support."""
    from app.config import settings
    host = settings.get_public_host(request.url.port)
    playlist_url = f"http://{host}/proxy/playlist/{session_id}"
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MyOwnTVApp - Now Playing</title>
<style>
  body {{ margin:0; background:#000; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  video {{ width:100%; max-width:1280px; }}
</style>
</head><body>
<video controls autoplay playsinline x-webkit-airplay="allow" airplay="allow"
       src="{playlist_url}"></video>
</body></html>"""
    return HTMLResponse(content=html)
