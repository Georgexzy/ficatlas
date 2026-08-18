"""FicAtlas Backend — FastAPI entry point"""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from api import (search, stories, stats, library, settings, auth, userdata,
                 takedown, password_reset, admin, permissions, hubs,
                 follows, traffic)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hand the loop to the background-job helper before anything can need it.
    # Request handlers run in a threadpool now (see below), and a thread cannot
    # call asyncio.create_task — it has to post to a loop it holds a reference to.
    try:
        import asyncio as _asyncio
        from live_fetch.jobs import bind_loop
        bind_loop(_asyncio.get_running_loop())
    except Exception:
        pass

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

    # Anonymous traffic is buffered in memory and written in batches; this is
    # the thing that writes it. Started here rather than in the worker because
    # the events are produced in THIS process — a worker task could not see
    # them. See tracking.py.
    import tracking
    track_task = asyncio.create_task(tracking.flush_loop()) if tracking.ENABLED else None

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
    if track_task is not None:
        # Cancelling is what triggers the final flush (see flush_loop), so the
        # last few seconds of traffic survive a deploy rather than being thrown
        # away on every promote.
        #
        # And it has to be AWAITED. cancel() only marks the task; the handler
        # that does the flushing needs the loop to run it once more, and lifespan
        # shutdown returns straight into teardown. Without this the buffered
        # events were dropped on every deploy — exactly what the comment above
        # claims is prevented.
        track_task.cancel()
        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await track_task
    if stop_scheduler is not None:
        stop_scheduler()

app = FastAPI(title="FicAtlas API", version="0.1.0", lifespan=lifespan)

# ── Rate limiting ────────────────────────────────────────────────────────────
# Only matters once this is reachable from outside the tailnet; see ratelimit.py
# for why it is per-IP-and-path-class rather than one global bucket.
from ratelimit import rate_limit_middleware, client_ip, is_internal_render  # noqa: E402

# ── Traffic: searches ────────────────────────────────────────────────────────
# Pageviews are reported by the app (POST /api/traffic/hit) because only the
# browser knows what it actually rendered. Searches are taken here instead, off
# the request itself, for the opposite reason: this is where they are
# authoritative. A query recorded server-side is the query that really ran,
# cannot be forged or double-counted by a re-render, and includes searches made
# by anything that is not our own page.
#
# Registered BEFORE the rate limiter, which means it sits INSIDE it — Starlette
# builds the stack so the last middleware added is the outermost. A throttled
# flood is therefore not recorded as search traffic, which is right: it is an
# attack, not an audience.
@app.middleware("http")
async def track_search_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        import tracking
        if (tracking.ENABLED
                and request.url.path.rstrip("/") == "/api/search"
                and response.status_code < 400
                and not is_internal_render(request)):
            q = (request.query_params.get("q") or "").strip()
            if q:
                ua = request.headers.get("user-agent", "")
                tracking.record(
                    "search", "/api/search",
                    tracking.visitor_hash(client_ip(request), ua),
                    q=q,
                    # Set by search() next to each of its exits. Absent rather
                    # than wrong if a new exit ever forgets it — a missing count
                    # shows as "—" in the report, which is honest, where a zero
                    # would read as "this search found nothing".
                    results=getattr(request.state, "search_total", None),
                    bot=tracking.is_bot(ua),
                )
    except Exception:
        pass  # analytics may never fail a request
    return response


app.middleware("http")(rate_limit_middleware)

# Outside the routing, so it sees the finished response — including the ones a
# path operation built itself, which is precisely the case where the session
# cookie set inside get_current_user never made it out. See that function.
app.middleware("http")(auth.reissue_session_cookie_middleware)

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

# Compress responses. Search results are the big ones — twenty stories with
# summaries, tag arrays and relationship lists is tens of kilobytes of JSON, and
# JSON of that shape compresses by roughly 8-10x. The CPU spent is trivial next
# to the query that produced it.
#
# This matters more here than on a normal deployment: the site is served from a
# domestic connection, where UPLOAD bandwidth is the scarce direction and is
# shared with everything else in the house. Bytes not sent are the cheapest
# capacity there is.
#
# minimum_size skips the small responses, where the header and the compression
# would cost more than they save.
app.add_middleware(GZipMiddleware, minimum_size=800)

app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(stories.router, prefix="/api/stories", tags=["stories"])
app.include_router(stats.router,  prefix="/api/stats", tags=["stats"])
# Crawlable entry points. Read-only; see fandom_hubs.py and ship_hubs.py.
app.include_router(hubs.router, prefix="/api/hubs", tags=["hubs"])
# Separate prefix rather than /api/hubs/ships, which would collide with the
# `/{slug}` route above the moment a fandom slugged to "ships".
app.include_router(hubs.ships_router, prefix="/api/ships", tags=["hubs"])
app.include_router(library.router, prefix="/api/library", tags=["library"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(takedown.router, prefix="/api/takedown", tags=["takedown"])
# Granting permission is a different act from asking for removal, and needs
# evidence where removal deliberately needs none — see api/permissions.py.
app.include_router(permissions.router, prefix="/api/permissions", tags=["permissions"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(password_reset.router, prefix="/api/auth", tags=["auth"])
app.include_router(userdata.router, prefix="/api/userdata", tags=["userdata"])
# Following a work across archives — the one subscription list AO3, FF.net and
# FictionAlley cannot give you between them. See api/follows.py.
app.include_router(follows.router, prefix="/api/follows", tags=["follows"])
# The beacon under this prefix is public; every report under it is owner-only.
# See api/traffic.py for why the reports sit at owner rather than admin.
app.include_router(traffic.router, prefix="/api/traffic", tags=["traffic"])

# ── Degrade honestly when the database is saturated ─────────────────────────
#
# A statement timeout or an exhausted connection pool raises OperationalError,
# which without this becomes a bare HTTP 500: "Internal Server Error", no
# Retry-After, and indistinguishable from a bug. Load testing at 8 concurrent
# searches produced 375 of those in 612 requests.
#
# 503 is what the condition actually is — the server is temporarily unable to
# handle the request — and it carries Retry-After, so a client and any cache in
# front of it both know to come back rather than treating the answer as final.
# The message says which part is busy, because "search is busy" is something a
# reader can act on and "Internal Server Error" is not.
@app.exception_handler(OperationalError)
async def _db_overloaded(request: Request, exc: OperationalError):
    logging.getLogger(__name__).warning(
        "database overloaded on %s: %s", request.url.path, str(exc)[:200])
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={"detail": "The index is busy right now. Try again in a moment."},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
