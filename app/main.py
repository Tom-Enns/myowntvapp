import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.services.extractor import StreamExtractor
from app.services.transcoder import TranscoderService
from app.routes import ui, api, proxy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


from app.services.logos import LogoService
from app.services.scraper import StreamScraper

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.extractor = StreamExtractor()
    await app.state.extractor.start()
    app.state.transcoder = TranscoderService()
    
    app.state.logos = LogoService()
    app.state.scraper = StreamScraper(app.state.logos)
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
