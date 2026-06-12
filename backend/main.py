"""FicAtlas Backend — FastAPI entry point"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import search, stories, crawl, stats, library

@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.include_router(crawl.router, prefix="/api/crawl", tags=["crawl"])
app.include_router(stats.router,  prefix="/api/stats", tags=["stats"])
app.include_router(library.router, prefix="/api/library", tags=["library"])

@app.get("/health")
async def health():
    return {"status": "ok"}
