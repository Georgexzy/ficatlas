"""Base crawler — all site adapters extend this"""
import asyncio
import httpx
from abc import ABC, abstractmethod
from typing import Optional
from models.story import Story, SiteEnum
from db.session import db_session


class BaseCrawler(ABC):
    SITE: SiteEnum
    RATE_LIMIT_DELAY = 2.0          # seconds between requests (be polite)
    MAX_RETRIES = 3
    HEADERS = {
        "User-Agent": "FicAtlas/0.1 (fanfiction discovery; contact: admin@ficatlas.app)",
        "Accept": "text/html,application/xhtml+xml",
    }

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=self.HEADERS,
            timeout=30,
            follow_redirects=True,
        )

    async def fetch(self, url: str) -> Optional[str]:
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
                await asyncio.sleep(self.RATE_LIMIT_DELAY)
                return resp.text
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = self.RATE_LIMIT_DELAY * (2 ** attempt)
                    print(f"[{self.SITE}] Rate limited — waiting {wait}s")
                    await asyncio.sleep(wait)
                elif e.response.status_code == 404:
                    return None
                else:
                    raise
            except httpx.RequestError as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(self.RATE_LIMIT_DELAY * 2)
        return None

    def upsert_story(self, db, data: dict) -> tuple[bool, bool]:
        """Insert or update a story. Returns (is_new, is_updated)."""
        existing = (
            db.query(Story)
            .filter(Story.site == self.SITE, Story.site_id == data["site_id"])
            .first()
        )
        if existing:
            changed = False
            for k, v in data.items():
                if getattr(existing, k, None) != v:
                    setattr(existing, k, v)
                    changed = True
            return False, changed
        else:
            story = Story(site=self.SITE, **data)
            db.add(story)
            return True, False

    @abstractmethod
    async def run(self, job_type: str = "incremental") -> dict:
        """Run the crawl. Return stats dict."""
        ...

    async def close(self):
        await self.client.aclose()
