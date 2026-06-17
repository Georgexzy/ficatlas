"""FicAtlas Backend — FastAPI entry point"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import search, stories, stats, library, settings, auth, userdata

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotently ensure schema is up-to-date (adds new columns safely)
    try:
        from init_db import init as init_db
        init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB init at startup failed: {e}")

    from scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()

app = FastAPI(title="FicAtlas API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(stories.router, prefix="/api/stories", tags=["stories"])
app.include_router(stats.router,  prefix="/api/stats", tags=["stats"])
app.include_router(library.router, prefix="/api/library", tags=["library"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(userdata.router, prefix="/api/userdata", tags=["userdata"])

@app.get("/health")
async def health():
    return {"status": "ok"}
