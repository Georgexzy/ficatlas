"""FicAtlas Backend — FastAPI entry point"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import search, stories, crawl

app = FastAPI(title="FicAtlas API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(stories.router, prefix="/api/stories", tags=["stories"])
app.include_router(crawl.router, prefix="/api/crawl", tags=["crawl"])

@app.get("/health")
async def health():
    return {"status": "ok"}
