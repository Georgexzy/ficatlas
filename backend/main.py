"""FicAtlas Backend — FastAPI entry point"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import (search, stories, stats, library, settings, auth, userdata,
                 takedown, password_reset, admin)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotently ensure schema is up-to-date (adds new columns safely)
    try:
        from init_db import init as init_db
        init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB init at startup failed: {e}")

    # Warm the index-status caches off the request path.
    #
    # /totals and /sites are whole-table aggregates (~10s at 19.7M rows, far more
    # under load) and they run on every page load. They serve stale values while
    # refreshing in the background, but that only helps once a value EXISTS — the
    # cache is empty after every restart, so the first visitor paid the full scan.
    # Measured 17-45s while the bulk jobs were running.
    async def _warm_caches():
        import asyncio
        from api.stats import _recompute_totals, _recompute_sites
        for fn in (_recompute_totals, _recompute_sites):
            try:
                await asyncio.to_thread(fn)
            except Exception:
                pass  # a cold cache is a slow page, not a broken one

    import asyncio
    warm_task = asyncio.create_task(_warm_caches())

    # Recurring background work lives in the worker container, not here: running
    # it on the API's event loop meant feed polls and crawls competed with request
    # handling. Set RUN_SCHEDULER=true to run it in-process instead (single-
    # container deployments), but never in both, or every feed gets polled twice.
    run_scheduler = os.getenv("RUN_SCHEDULER", "false").strip().lower() in ("1", "true", "yes")
    stop_scheduler = None
    if run_scheduler:
        from scheduler import start_scheduler, stop_scheduler
        start_scheduler()

    yield

    warm_task.cancel()
    if stop_scheduler is not None:
        stop_scheduler()

app = FastAPI(title="FicAtlas API", version="0.1.0", lifespan=lifespan)

# ── Rate limiting ────────────────────────────────────────────────────────────
# Only matters once this is reachable from outside the tailnet; see ratelimit.py
# for why it is per-IP-and-path-class rather than one global bucket.
from ratelimit import rate_limit_middleware  # noqa: E402

app.middleware("http")(rate_limit_middleware)

# ── CORS ─────────────────────────────────────────────────────────────────────
# The browser never talks to this port directly: the Next.js frontend declares a
# rewrite for /api/* (see frontend/next.config.ts), so every request the browser
# makes is same-origin against port 3000 and needs no CORS headers at all.
#
# The previous config (allow_origins=["*"] + allow_credentials=True) was actively
# harmful. Starlette cannot send a literal "*" alongside credentials, so it echoes
# the request's own Origin back instead — meaning ANY website the user happened to
# be visiting could call this API with the session cookie attached and read the
# response (reading history, bookmarks, session list, settings).
#
# Default to no cross-origin access. Set FICATLAS_CORS_ORIGINS to a comma-separated
# list of exact origins only if you need to hit port 8000 from another origin.
_origins = [o.strip() for o in os.getenv("FICATLAS_CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,      # exact origins only — never "*" with credentials
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(stories.router, prefix="/api/stories", tags=["stories"])
app.include_router(stats.router,  prefix="/api/stats", tags=["stats"])
app.include_router(library.router, prefix="/api/library", tags=["library"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(takedown.router, prefix="/api/takedown", tags=["takedown"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(password_reset.router, prefix="/api/auth", tags=["auth"])
app.include_router(userdata.router, prefix="/api/userdata", tags=["userdata"])

@app.get("/health")
async def health():
    return {"status": "ok"}
