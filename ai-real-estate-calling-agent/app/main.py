"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.config import settings
from app.logging_config import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup / shutdown lifecycle."""
    configure_logging()
    log = get_logger("app.main")
    log.info(
        "application startup",
        extra={"ctx": {"environment": settings.ENVIRONMENT, "mock_mode": settings.is_mock}},
    )
    await _prewarm_greeting_audio(log)
    yield
    log.info("application shutdown")


async def _prewarm_greeting_audio(log) -> None:
    """Synthesise the opening greeting once at startup.

    The TTS provider is a process-wide singleton with an LRU cache, so this
    one call (a few paise) makes the greeting serve from cache on every real
    call — the customer hears the agent almost immediately after answering
    instead of waiting through a cold Sarvam synthesis. Best-effort: startup
    must never fail because of it.
    """
    try:
        if settings.is_mock:
            return
        from app.conversation.engine import ConversationEngine
        from app.conversation.states import ConvState
        from app.providers import get_tts_provider
        from app.services.live_agent import DEFAULT_PERSONA

        engine = ConversationEngine()
        turn = await engine.step(
            state=ConvState.GREETING, first_turn=True, persona=DEFAULT_PERSONA
        )
        if turn.reply:
            await get_tts_provider().synthesize(turn.reply)
            log.info("greeting audio pre-warmed")
    except Exception as exc:
        log.warning("greeting pre-warm skipped", extra={"ctx": {"error": str(exc)}})


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Low-cost AI Real Estate Cold Calling Agent (Hinglish).",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # CORS - restrict origins in production; permissive only in development.
    allowed_origins = ["*"] if settings.ENVIRONMENT == "development" else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request-body size guard - reject oversized bodies (hardening).
    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "request body too large"},
                    )
            except (TypeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "invalid content-length"},
                )
        return await call_next(request)

    app.include_router(health_router)
    app.include_router(v1_router)

    # Lightweight operator dashboard - served as static files from the SAME
    # FastAPI app (NO separate frontend server). Rustles against the existing
    # /api/v1 endpoints directly from the browser.
    dashboard_dir = Path(__file__).resolve().parent / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = dashboard_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    data_dir = dashboard_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
            "ready": "/ready",
            "dashboard": "/dashboard/",
        }

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_home():
        return RedirectResponse(url="/dashboard/")

    app.mount(
        "/dashboard",
        StaticFiles(directory=dashboard_dir, html=True),
        name="dashboard",
    )

    return app


app = create_app()
