"""Hub pages — the crawlable way into the index.

Two kinds, served identically: one per fandom (fandom_hubs) and one per romantic
pairing (ship_hubs). Read-only and cheap by construction: both tables hold
precomputed rows, so a hub is a primary-key lookup plus a fetch of ~150 stories
by id. Nothing here ranks or scans, because these are the routes a crawler hits
hardest.

See fandom_hubs.py for why hubs exist at all, and ship_hubs.py for why pairings
get their own set rather than being a filter on a fandom hub.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db

router = APIRouter()
ships_router = APIRouter()


class HubSummary(BaseModel):
    slug: str
    name: str
    work_count: int
    # Ships only. Shown on the index so the A-Z is browsable by the name people
    # know the pairing by, not only by the canonical tag.
    nicknames: list[str] = []
    # When the hub's CONTENTS last changed (not when it was last rebuilt) — the
    # sitemap's <lastmod>. See the column note in init_db.py.
    content_at: Optional[str] = None


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


class RelatedHub(BaseModel):
    """Another hub worth a link from this one. `kind` is "fandom" or "ship"."""
    kind: str
    slug: str
    name: str
    work_count: int


class HubDetail(BaseModel):
    slug: str
    name: str
    work_count: int
    # Fandom names for a pairing ("Drarry"), primary first. Ships only; empty
    # for fandoms and for the many pairings that have no portmanteau. See
    # SHIP_NICKNAMES in ship_hubs.py for why these are curated, not derived.
    nicknames: list[str] = []
    # Flat, interleaved across archives. Kept for anything reading the old shape.
    works: list[HubWork]
    sections: list[SiteSection]
    # Hubs worth walking to from here. See `_related` — this is the site's only
    # lateral link, and until it existed every hub was a crawl dead end
    # sideways: the index linked 11,190 hubs, each hub linked 100 story pages,
    # and no hub linked to any other.
    related: list[RelatedHub] = []


# Both hub tables have the same shape, so listing and detail differ only in the
# table name. It is interpolated, so it is checked against a literal allowlist
# rather than trusted — these are module constants today and this keeps that
# safe if a caller ever passes something derived from a request.
_TABLES = {"fandom": "fandom_hubs", "ship": "ship_hubs"}

CACHE = "public, max-age=3600, stale-while-revalidate=86400"


def _list(kind: str, response: Response, limit: int, offset: int, db: Session):
    """Every hub of one kind, largest first. Backs the index pages and the
    sitemap.

    Fully public and rebuilt offline, so a shared cache can hold it for a long
    time — this is the request a crawler makes before walking every hub."""
    table = _TABLES[kind]
    response.headers["Cache-Control"] = CACHE
    rows = db.execute(text(f"""
        SELECT slug, name, work_count, content_at FROM {table}
         ORDER BY work_count DESC, slug
         LIMIT :lim OFFSET :off
    """), {"lim": limit, "off": offset}).fetchall()
    nick = {}
    if kind == "ship":
        from ship_hubs import nicknames_for
        nick = {r[0]: nicknames_for(r[0]) for r in rows}
    return [HubSummary(slug=r[0], name=r[1], work_count=r[2],
                       nicknames=nick.get(r[0], []),
                       content_at=r[3].isoformat() if r[3] else None)
            for r in rows]


# How many lateral links a hub offers. Enough to be a real path for a crawler
# and a real choice for a reader; few enough that the page is still about the
# thing it is about.
RELATED_CAP = 8


def _related(db, kind: str, slug: str, name: str,
             fandoms: list[str], rels: list[str]) -> list[RelatedHub]:
    """Hubs worth linking to from this one.

    Until this existed the site was two levels deep and had no sideways edges:
    `/ships` linked all 6,165 ship hubs, each ship hub linked 100 story pages,
    and no hub linked to any other hub. Measured consequence — Googlebot, which
    crawls this site 119 times a day, had reached 90 DISTINCT hubs in the whole
    of the retained log. A crawler that lands on one pairing from a search
    result has nowhere to go but back out, and no authority flows between the
    pages that actually earn traffic (56% of all referred visits land on a ship
    hub).

    Derived at read time from the works already loaded for the page, so there
    is no new column, no rebuild, and nothing to fall out of date. `variants`
    holds every archive spelling of a hub's subject, which is what lets a raw
    fandom or relationship string off a work be matched back to its hub.

    Never raises: a hub page that renders without its related links is a page,
    and one that 500s is not.
    """
    def _modal(values: list[str], top: int) -> list[str]:
        counts: dict[str, int] = {}
        for v in values:
            if v:
                counts[v] = counts.get(v, 0) + 1
        return [v for v, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]

    out: list[RelatedHub] = []
    seen = {slug}
    try:
        if kind == "ship":
            # The fandom this pairing lives in. One link, and the most valuable
            # one: it is the only edge from a niche pairing up into a page with
            # standing.
            for f in _modal(fandoms, 2):
                row = db.execute(text(
                    "SELECT slug, name, work_count FROM fandom_hubs "
                    " WHERE :v = ANY(variants) ORDER BY work_count DESC LIMIT 1"
                ), {"v": f}).fetchone()
                if row and row[0] not in seen:
                    seen.add(row[0])
                    out.append(RelatedHub(kind="fandom", slug=row[0],
                                          name=row[1], work_count=row[2]))
                    break
            # Other pairings for the same characters. "If you read Castiel/Dean,
            # here is everything else either of them is shipped with" is a real
            # reader question, and it is the edge that connects the long tail of
            # pairings to each other rather than only to the A-Z index.
            halves = [h.strip() for h in name.split("/") if len(h.strip()) > 2]
            # Taken a slice at a time from EACH half in turn, not all of one
            # then all of the other. Ordering by work_count across a single
            # query filled the whole cap with "Castiel/..." and never reached
            # Dean, which answers half the question a reader came with.
            per_half = [db.execute(text(
                "SELECT slug, name, work_count FROM ship_hubs "
                " WHERE name ILIKE :pat AND slug <> :self"
                " ORDER BY work_count DESC LIMIT :lim"
            ), {"pat": f"%{half}%", "self": slug,
                "lim": RELATED_CAP}).fetchall() for half in halves]
            for i in range(RELATED_CAP):
                for rows in per_half:
                    if len(out) >= RELATED_CAP or i >= len(rows):
                        continue
                    r = rows[i]
                    if r[0] not in seen:
                        seen.add(r[0])
                        out.append(RelatedHub(kind="ship", slug=r[0],
                                              name=r[1], work_count=r[2]))
        else:
            # A fandom's most-written pairings. The fandom hubs are the ones
            # that cannot outrank AO3 for their own name, so their job is to
            # pass a crawler on to the ship hubs, which can.
            for rel in _modal(rels, RELATED_CAP * 2):
                if len(out) >= RELATED_CAP:
                    break
                row = db.execute(text(
                    "SELECT slug, name, work_count FROM ship_hubs "
                    " WHERE :v = ANY(variants) ORDER BY work_count DESC LIMIT 1"
                ), {"v": rel}).fetchone()
                if row and row[0] not in seen:
                    seen.add(row[0])
                    out.append(RelatedHub(kind="ship", slug=row[0],
                                          name=row[1], work_count=row[2]))
    except Exception:
        db.rollback()
        return []
    return out[:RELATED_CAP]


def _detail(kind: str, slug: str, response: Response, db: Session) -> HubDetail:
    table = _TABLES[kind]
    response.headers["Cache-Control"] = CACHE
    hub = db.execute(text(
        f"SELECT slug, name, work_count, top_ids, top_by_site "
        f"  FROM {table} WHERE slug = :s"
    ), {"s": slug}).fetchone()
    if not hub:
        raise HTTPException(status_code=404,
                            detail="No such fandom" if kind == "fandom"
                                   else "No such pairing")

    ids = list(hub[3] or [])
    works: list[HubWork] = []
    _fandoms: list[str] = []
    _rels: list[str] = []
    if ids:
        # Re-checking delisted/restricted at read time rather than trusting the
        # snapshot: a hub may be hours or days old, and a work withdrawn since
        # the build must not keep appearing on an indexed page. WITH ORDINALITY
        # preserves the precomputed ranking without re-sorting by kudos here.
        rows = db.execute(text("""
            SELECT s.id, s.title, s.author, s.summary, s.word_count,
                   s.chapter_count, s.kudos, s.site, s.status,
                   -- Only for _related below. Free here: these rows are
                   -- already being read, and the alternative is a second pass
                   -- over the same works.
                   s.fandoms, s.relationships
              FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS t(id, ord)
              JOIN stories s ON s.id = t.id
             WHERE s.delisted_at IS NULL
               AND s.source_restricted_at IS NULL
             ORDER BY t.ord
        """), {"ids": ids}).fetchall()
        _fandoms = [f for r in rows for f in (r[9] or [])]
        _rels = [v for r in rows for v in (r[10] or [])]
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

    nicknames: list[str] = []
    if kind == "ship":
        from ship_hubs import nicknames_for
        nicknames = nicknames_for(hub[0])

    related = _related(db, kind, hub[0], hub[1], _fandoms, _rels)

    return HubDetail(slug=hub[0], name=hub[1], work_count=hub[2],
                     nicknames=nicknames, works=works, sections=sections,
                     related=related)


@router.get("", response_model=list[HubSummary])
def list_hubs(
    response: Response,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _list("fandom", response, limit, offset, db)


@router.get("/{slug}", response_model=HubDetail)
def get_hub(slug: str, response: Response, db: Session = Depends(get_db)):
    return _detail("fandom", slug, response, db)


@ships_router.get("", response_model=list[HubSummary])
def list_ships(
    response: Response,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _list("ship", response, limit, offset, db)


@ships_router.get("/{slug}", response_model=HubDetail)
def get_ship(slug: str, response: Response, db: Session = Depends(get_db)):
    return _detail("ship", slug, response, db)
