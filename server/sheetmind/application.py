"""FastAPI application factory."""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from sheetmind import __version__
from sheetmind.database import init_db
from sheetmind.exceptions import SheetMindException, safe_error_message
from sheetmind.config import settings
from sheetmind.logging import configure_logging
from sheetmind.services.anonymous_sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_DAYS,
)

from .api.analysis import router as analysis_router
from .api.conversations import router as conversations_router
from .api.files import router as files_router
from .api.projects import router as projects_router
from .api.tasks import router as tasks_router
from .api.dependencies import anonymous_sessions


ALLOWED_ORIGINS = [
    "http://localhost:3002",
    "http://127.0.0.1:3002",
]


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_dir)
    init_db()
    app = FastAPI(title="SheetMind API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_anonymous_session(request: Request, call_next):
        session = anonymous_sessions.resolve(request.cookies.get(SESSION_COOKIE_NAME))
        request.state.anonymous_session_id = session.session_id
        response = await call_next(request)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.session_id,
            max_age=SESSION_LIFETIME_DAYS * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=settings.session_cookie_secure,
        )
        return response

    @app.exception_handler(SheetMindException)
    async def handle_sheetmind_error(_: Request, exc: SheetMindException):
        return JSONResponse(status_code=400, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": safe_error_message(exc)})

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "SheetMind API", "version": __version__}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    for router in (
        projects_router,
        tasks_router,
        files_router,
        analysis_router,
        conversations_router,
    ):
        app.include_router(router, prefix="/api")

    return app


app = create_app()
