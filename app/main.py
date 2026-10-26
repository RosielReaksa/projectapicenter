"""FastAPI application."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__, jobs
from app.api import (
    admin_access,
    admin_activity,
    admin_auth,
    admin_keys,
    admin_misc,
    admin_partners,
    admin_providers,
    admin_routing,
    admin_usage,
    health,
    public,
    v1_chat,
)
from app.bootstrap import bootstrap
from app.config import BASE_DIR, settings
from app.core import settings_store
from app.core.store import build_store, close_store
from app.db import SessionLocal, dispose_db, init_db
from app.errors import error_response, install_error_handlers
from app.logging_config import configure_logging
from app.utils import new_request_id

configure_logging()
log = logging.getLogger("gateway")

STATIC_DIR = BASE_DIR / "app" / "static"
CSRF_COOKIE = "gw_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT = {"/admin/auth/login"}

app_http: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_http

    await init_db()
    async with SessionLocal() as session:
        await bootstrap(session)
        await settings_store.load_all(session)

    store = build_store()
    if not await store.healthy():
        log.warning("Shared state store did not answer its health check at startup.")

    app_http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.provider_timeout_seconds, connect=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=True,
    )
    app.state.http = app_http

    jobs.start()
    log.info(
        "gateway ready version=%s env=%s shared_state=%s",
        __version__,
        settings.environment,
        "redis" if settings.redis_url else "memory",
    )

    try:
        yield
    finally:
        # Graceful shutdown: stop accepting, let in-flight work finish, then close.
        jobs.stop()
        await app_http.aclose()
        app_http = None
        await close_store()
        await dispose_db()
        log.info("gateway stopped cleanly")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

install_error_handlers(app)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-End-User"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or new_request_id()

    # Reject oversized bodies before they reach the parser.
    length = request.headers.get("content-length")
    limit = int(settings_store.get("max_request_bytes", settings.max_request_bytes) or settings.max_request_bytes)
    if length and length.isdigit() and int(length) > limit:
        return error_response(
            413, "payload_too_large", f"Request body exceeds {limit} bytes.", request.state.request_id
        )

    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    )
    # CSP: the console is a self-contained SPA. No third-party scripts, and
    # inline styles are needed by React's style props and Recharts.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Double-submit token for state-changing control-plane calls."""
    if (
        request.method not in SAFE_METHODS
        and request.url.path.startswith("/admin")
        and request.url.path not in CSRF_EXEMPT
    ):
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("x-csrf-token", "")
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return error_response(
                403, "csrf_failed", "Missing or invalid CSRF token. Reload the page.", ""
            )
    return await call_next(request)


# ------------------------------------------------------------------ routers
app.include_router(health.router)
app.include_router(public.router)
app.include_router(v1_chat.router)
app.include_router(admin_auth.router)
app.include_router(admin_access.router)
app.include_router(admin_partners.router)
app.include_router(admin_keys.router)
app.include_router(admin_providers.router)
app.include_router(admin_providers.models_router)
app.include_router(admin_routing.router)
app.include_router(admin_routing.presets_router)
app.include_router(admin_routing.lookup_router)
app.include_router(admin_activity.router)
app.include_router(admin_usage.router)
app.include_router(admin_misc.router)


@app.get("/api/config", tags=["meta"])
async def public_config():
    """Unauthenticated payload the login screen needs."""
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "version": __version__,
    }


PUBLIC_PATHS = ["/", "/models", "/docs", "/request-access", "/terms", "/privacy"]


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request):
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /console\n"
        "Disallow: /login\n"
        "Disallow: /admin\n"
        "Disallow: /v1\n"
        "Disallow: /api\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    urls = "".join(
        f"<url><loc>{base}{path}</loc><changefreq>weekly</changefreq></url>"
        for path in PUBLIC_PATHS
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return Response(content=body, media_type="application/xml")


# ---------------------------------------------------------------- static SPA
if (STATIC_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse(
            {
                "app": settings.app_name,
                "version": __version__,
                "message": "Console not built yet. Run `npm install && npm run build` in ./frontend.",
                "docs": "/api/docs",
            }
        )
