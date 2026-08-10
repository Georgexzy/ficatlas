"""Fandom hub pages — the crawlable way into the index.

Read-only and cheap by construction: fandom_hubs holds precomputed rows, so a
hub is a primary-key lookup plus a fetch of ~60 stories by id. Nothing here
ranks or scans, because these are the routes a crawler hits hardest.

See fandom_hubs.py for why hubs exist and how they are built.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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


class SiteSection(BaseModel):
    """One archive's top works. Ranked within the site — see fandom_hubs.py for
    why a single cross-archive ranking could only ever return AO3."""
    site: str
    works: list[HubWork]


class HubDetail(BaseModel):
    slug: str
    name: str
    work_count: int
    # Flat, interleaved across archives. Kept for anything reading the old shape.
    works: list[HubWork]
    sections: list[SiteSection]


@router.get("", response_model=list[HubSummary])
def list_hubs(
    response: Response,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Every hub, largest first. Backs the /fandoms index page and the sitemap.

    Fully public and rebuilt offline, so a shared cache can hold it for a long
    time — this is the request a crawler makes before walking every hub."""
    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    rows = db.execute(text("""
        SELECT slug, name, work_count FROM fandom_hubs
         ORDER BY work_count DESC, slug
         LIMIT :lim OFFSET :off
    """), {"lim": limit, "off": offset}).fetchall()
    return [HubSummary(slug=r[0], name=r[1], work_count=r[2]) for r in rows]


@router.get("/{slug}", response_model=HubDetail)
def get_hub(slug: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    hub = db.execute(text(
        "SELECT slug, name, work_count, top_ids, top_by_site "
        "  FROM fandom_hubs WHERE slug = :s"
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

    # Group into per-archive sections using the stored per-site ordering. Built
    # from the same `works` list, so the delisted/restricted re-check above
    # applies to the sections too rather than being done twice.
    by_id = {w.id: w for w in works}
    sections: list[SiteSection] = []
    for site, site_ids in (hub[4] or {}).items():
        picked = [by_id[i] for i in site_ids if i in by_id]
        if picked:
            sections.append(SiteSection(site=site, works=picked))
    # Largest archive first, so the biggest list leads the page.
    sections.sort(key=lambda s: -len(s.works))

    return HubDetail(slug=hub[0], name=hub[1], work_count=hub[2],
                     works=works, sections=sections)
