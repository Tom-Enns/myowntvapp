import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.services.extractor import StreamExtractor
from app.services.transcoder import TranscoderService
from app.services.logos import LogoService
from app.backends.registry import BackendRegistry
from app.backends.thetvapp import create_backend as create_thetvapp_backend
from app.schedule.registry import ScheduleRegistry
from app.schedule.thetvapp_schedule import create_provider as create_thetvapp_schedule
from app.schedule.sportsdb import create_provider as create_sportsdb_schedule
from app.schedule.nhl_schedule import create_provider as create_nhl_schedule
from app.routes import ui, api, proxy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Legacy extractor (kept for backward compat, used by thetvapp backend internally)
    app.state.extractor = StreamExtractor()
    await app.state.extractor.start()
    app.state.transcoder = TranscoderService()

    # Shared services
    app.state.logos = LogoService()

    # Schedule registry
    schedule_registry = ScheduleRegistry()
    thetvapp_schedule = create_thetvapp_schedule(app.state.logos)
    schedule_registry.register(thetvapp_schedule)
    sportsdb_schedule = create_sportsdb_schedule(app.state.logos)
    schedule_registry.register(sportsdb_schedule)
    nhl_schedule = create_nhl_schedule()
    schedule_registry.register(nhl_schedule)
    schedule_registry.set_primary(settings.SCHEDULE_PROVIDER)
    app.state.schedule_registry = schedule_registry

    # Backend registry
    backend_registry = BackendRegistry()
    backend_registry.register(create_thetvapp_backend())
    backend_registry.set_priority(settings.BACKEND_PRIORITY)
    app.state.backend_registry = backend_registry

    yield

    await app.state.transcoder.stop_all()
    await app.state.extractor.stop()


app = FastAPI(title="MyOwnTVApp", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.include_router(ui.router)
app.include_router(api.router, prefix="/api")
app.include_router(proxy.router, prefix="/proxy")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
