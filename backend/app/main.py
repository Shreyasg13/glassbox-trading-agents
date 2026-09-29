from __future__ import annotations

import os
import time
import uuid

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware

from .logging_config import request_id_var, setup_logging
from .routers import admin, auth, committee, data, insights, jobs_ws, ledger, me, monte_carlo, oauth, paper, reports, strategy, tts, ws
from .routers import analytics as analytics_routes
from .routers import flags as flags_routes
from .routers import inbox as inbox_routes
from .routers import public as public_routes
from .routers import user_digest as user_digest_routes
from .auth import require_role

app = FastAPI(
    title="GlassBox API",
    description="FastAPI gateway over the multi-agent-trading-system engine.",
    version="0.1.0",
)

request_logger = setup_logging()

# CORS_ALLOWED_ORIGINS: comma-separated list, e.g.
# "http://localhost:3000,https://app.yourdomain.com". Defaults to the local
# dev origin only -- production deployments must set this explicitly
# (docker-compose.yml derives it from APP_DOMAIN) or browser-based logins
# from the deployed frontend are silently blocked by CORS while curl/direct
# API calls keep working, since CORS is enforced by the browser, not the API.
_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    # Only what the frontend actually uses -- "*" with credentials=True
    # would let any header/method through for the allowed origins.
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "X-TTS-Provider"],
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Baseline hardening headers on every API response (Caddy adds the
    same set plus HSTS for the frontend pages -- see deploy/Caddyfile).
    /auth responses carry bearer tokens, so they must never be cached by
    a browser or an intermediate proxy."""
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_tracing_middleware(request: Request, call_next):
    """Assigns a request id, echoes it back as X-Request-ID, and logs one
    structured line per request. Deliberately logs no headers/bodies --
    see logging_config.py for why."""
    request_id = str(uuid.uuid4())
    token = request_id_var.set(request_id)
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        request_id_var.reset(token)
        raise
    request_id_var.reset(token)
    duration_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    request_logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
        },
    )
    return response


app.include_router(data.router)
app.include_router(monte_carlo.router)
app.include_router(ws.router)
app.include_router(jobs_ws.router)
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(admin.router)
app.include_router(paper.router)
app.include_router(committee.router)
app.include_router(strategy.admin_router)
app.include_router(strategy.me_router)
app.include_router(me.router)
app.include_router(analytics_routes.public_router)
app.include_router(analytics_routes.admin_router)
app.include_router(flags_routes.router)
app.include_router(inbox_routes.me_router)
app.include_router(inbox_routes.admin_router)
app.include_router(user_digest_routes.me_router)
app.include_router(user_digest_routes.public_router)
app.include_router(public_routes.router)
app.include_router(reports.router)
app.include_router(insights.router)
app.include_router(tts.router)
app.include_router(ledger.router)


@app.get("/health", tags=["meta"], dependencies=[Depends(require_role("public"))])
async def health():
    return {"status": "ok"}
