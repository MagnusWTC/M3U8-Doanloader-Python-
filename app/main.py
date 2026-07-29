import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import router
from app.config import get_settings
from app.supervisor import TaskSupervisor


def create_app(start_supervisor: bool = True) -> FastAPI:
    supervisor = TaskSupervisor()
    app_root = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=app_root / "templates")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        supervisor_task = asyncio.create_task(supervisor.run()) if start_supervisor else None
        try:
            yield
        finally:
            if supervisor_task:
                await supervisor.shutdown()
                supervisor_task.cancel()
                await asyncio.gather(supervisor_task, return_exceptions=True)

    app = FastAPI(
        title="M3U8 Downloader",
        version="0.1.0",
        description="Resumable HLS downloads with MP4 output.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=app_root / "static"), name="static")
    app.include_router(router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    async def readiness() -> dict[str, str]:
        settings = get_settings()
        missing = [binary for binary in ("ffmpeg", "ffprobe") if not shutil.which(binary)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Missing required binaries: {', '.join(missing)}",
            )
        settings.ensure_directories()
        return {"status": "ready"}

    return app


app = create_app()
