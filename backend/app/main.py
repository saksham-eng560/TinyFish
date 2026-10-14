"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api import (
    agent,
    analytics,
    applications,
    auth,
    communications,
    demo_site,
    files,
    interviews,
    jobs,
    onboarding,
    resumes,
    review,
    users,
)
from app.api.deps import default_rate_limit, extract_token, limiter
from app.config import settings
from app.core.database import create_all, engine, wait_for_db
from app.core.logging_config import configure_logging
from app.core.redis import get_redis
from app.core.security import TokenError, decode_token
from app.core.websocket import manager
from app.services import llm_usage
from app.services.llm import get_llm

configure_logging()
logger = logging.getLogger(__name__)


def _ensure_demo_account() -> None:
    from app.core.database import session_scope
    from app.services.demo_seed import ensure_demo_account

    try:
        with session_scope() as db:
            ensure_demo_account(db)
    except Exception:  # never keep the API from starting; the demo sign-in seeds it on first use instead
        logger.exception("Could not create the demo account at start-up")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.require_production_ready()  # no default SECRET_KEY, no missing ENCRYPTION_KEY, cookies over HTTPS only
    await asyncio.to_thread(wait_for_db)
    if settings.is_sqlite:
        # Zero-setup local mode: create tables directly (PostgreSQL uses Alembic migrations).
        await asyncio.to_thread(create_all)
    if settings.DEMO_MODE:  # the demo sign-in needs its account from the first request on
        await asyncio.to_thread(_ensure_demo_account)
    manager.bind_loop(asyncio.get_running_loop())
    await manager.start_subscriber()
    llm = get_llm()
    logger.info("HireFlow API ready (env=%s, llm=%s, db=%s)", settings.ENVIRONMENT,
                llm.provider_names or "heuristics-only", engine.dialect.name)
    yield
    await manager.stop_subscriber()


app = FastAPI(
    title="HireFlow",
    version="1.0.0",
    description="Autonomous job application agent — human approval required before every submission.",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    dependencies=[Depends(default_rate_limit)],
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Synchronous on purpose: SlowAPIMiddleware only calls a sync handler (it falls back to its own otherwise).
@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": f"Rate limit exceeded: {exc.detail}"}, status_code=429)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else url.lower()


@app.middleware("http")
async def csrf_and_https(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Cookie-authenticated changes must come from the dashboard: a page on another site can't use your session.
    (Bearer tokens, used by the extension and scripts, can't be sent by another site, so they're not checked.)
    In production, plain-HTTP requests are redirected to HTTPS."""
    if settings.is_production and request.headers.get("x-forwarded-proto", "").lower() == "http":
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
    if (request.method in UNSAFE_METHODS and request.cookies.get(settings.COOKIE_NAME)
            and not request.headers.get("authorization")):
        source = request.headers.get("origin") or request.headers.get("referer")
        if source is not None and _origin_of(source) not in settings.trusted_origins:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def attribute_ai_usage(request: Request, call_next):  # type: ignore[no-untyped-def]
    """AI calls made while serving a signed-in request count against that user's daily budget."""
    user_id = None
    token = extract_token(request)
    if token:
        try:
            user_id = decode_token(token, expected_scopes=("access", "extension"))["sub"]
        except (TokenError, KeyError):
            user_id = None
    with llm_usage.for_user(user_id):
        return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # resume PDFs preview in same-origin iframes
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


for router in (auth.router, onboarding.router, users.router, resumes.router, jobs.router, applications.router, agent.router,
               communications.router, interviews.router, analytics.router, review.router, files.router, demo_site.router):
    app.include_router(router, prefix=settings.API_PREFIX)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
    if not settings.REDIS_URL:  # a single-container deployment runs its tasks in-process by design
        checks["redis"] = "not used (in-process tasks)"
    else:
        checks["redis"] = "ok" if get_redis() is not None else "unavailable (in-process fallback)"
    checks["llm"] = ", ".join(get_llm().provider_names) or "not configured (heuristic mode)"
    healthy = checks["database"] == "ok"
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503)
