"""Fandom hub pages — the crawlable way into the index.

Read-only and cheap by construction: fandom_hubs holds precomputed rows, so a
hub is a primary-key lookup plus a fetch of ~60 stories by id. Nothing here
ranks or scans, because these are the routes a crawler hits hardest.

See fandom_hubs.py for why hubs exist and how they are built.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db

router = APIRouter()


class HubSummary(BaseModel):
    slug: str
    name: str
    work_count: int


class HubWork(BaseModel):
    id: str
    title: str
    author: Optional[str] = None
    summary: Optional[str] = None
    word_count: Optional[int] = None
    chapter_count: Optional[int] = None
    kudos: Optional[int] = None
    site: Optional[str] = None
    complete: Optional[bool] = None


class HubDetail(BaseModel):
    slug: str
    name: str
    work_count: int
    works: list[HubWork]


@router.get("", response_model=list[HubSummary])
def list_hubs(
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Every hub, largest first. Backs the /fandoms index page and the sitemap."""
    rows = db.execute(text("""
        SELECT slug, name, work_count FROM fandom_hubs
         ORDER BY work_count DESC, slug
         LIMIT :lim OFFSET :off
    """), {"lim": limit, "off": offset}).fetchall()
    return [HubSummary(slug=r[0], name=r[1], work_count=r[2]) for r in rows]


@router.get("/{slug}", response_model=HubDetail)
def get_hub(slug: str, db: Session = Depends(get_db)):
    hub = db.execute(text(
        "SELECT slug, name, work_count, top_ids FROM fandom_hubs WHERE slug = :s"
    ), {"s": slug}).fetchone()
    if not hub:
        raise HTTPException(status_code=404, detail="No such fandom")

    ids = list(hub[3] or [])
    works: list[HubWork] = []
    if ids:
        # Re-checking delisted/restricted at read time rather than trusting the
        # snapshot: a hub may be hours or days old, and a work withdrawn since
        # the build must not keep appearing on an indexed page. WITH ORDINALITY
        # preserves the precomputed ranking without re-sorting by kudos here.
        rows = db.execute(text("""
            SELECT s.id, s.title, s.author, s.summary, s.word_count,
                   s.chapter_count, s.kudos, s.site, s.status
              FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS t(id, ord)
              JOIN stories s ON s.id = t.id
             WHERE s.delisted_at IS NULL
               AND s.source_restricted_at IS NULL
             ORDER BY t.ord
        """), {"ids": ids}).fetchall()
        works = [
            HubWork(
                id=str(r[0]), title=r[1], author=r[2], summary=r[3],
                word_count=r[4], chapter_count=r[5], kudos=r[6], site=r[7],
                complete=(str(r[8]).lower() in ("complete", "completed")
                          if r[8] is not None else None),
            )
            for r in rows
        ]

    return HubDetail(slug=hub[0], name=hub[1], work_count=hub[2], works=works)
