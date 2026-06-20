"""Stats endpoint — per-site counts, totals, last-updated info"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from db.session import get_db
from models.story import Story

router = APIRouter()

# Map the autocomplete "kind" to the story array column it comes from.
_FACET_COLUMNS = {
    "fandom": "fandoms",
    "relationship": "relationships",
    "character": "characters",
    "tag": "tags",
}


@router.get("/sites")
async def site_stats(db: Session = Depends(get_db)):
    rows = (db.query(Story.site, func.count(Story.id), func.max(Story.indexed_at))
            .group_by(Story.site).all())
    return [
        {
            "site":  r[0].value if hasattr(r[0], "value") else str(r[0]),
            "count": r[1],
            "last_indexed": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]

@router.get("/totals")
async def total_stats(db: Session = Depends(get_db)):
    stories = db.query(func.count(Story.id)).scalar() or 0
    hosted  = db.query(func.count(Story.id)).filter(Story.is_hosted == True).scalar() or 0
    total_words = db.query(func.sum(Story.word_count)).scalar() or 0
    dlp = db.query(func.count(Story.id)).filter(
        Story.tags.any("dlp_library")  # type: ignore[arg-type]
    ).scalar() or 0
    hpffa = db.query(func.count(Story.id)).filter(
        Story.tags.any("hpffa_archive")  # type: ignore[arg-type]
    ).scalar() or 0
    return {"stories": stories, "hosted": hosted, "total_words": int(total_words),
            "dlp": dlp, "hpffa": hpffa}


@router.get("/suggest")
async def suggest(
    kind: str = Query("fandom"),
    q: str = Query("", min_length=0),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Tag autocomplete. Reads from the precomputed `facets` table (fast).
    If facets are empty (never refreshed), falls back to a bounded live scan so
    the feature still works, just slower. kind ∈ fandom|relationship|character|tag."""
    if kind not in _FACET_COLUMNS:
        kind = "fandom"
    ql = q.strip().lower()

    # Try the precomputed facet table first.
    try:
        if ql:
            rows = db.execute(text(
                "SELECT value, count FROM facets "
                "WHERE kind = :kind AND value ILIKE :pat "
                "ORDER BY count DESC LIMIT :lim"
            ), {"kind": kind, "pat": f"%{ql}%", "lim": limit}).fetchall()
        else:
            rows = db.execute(text(
                "SELECT value, count FROM facets WHERE kind = :kind "
                "ORDER BY count DESC LIMIT :lim"
            ), {"kind": kind, "lim": limit}).fetchall()
        if rows:
            return [{"value": r[0], "count": r[1]} for r in rows]
    except Exception:
        pass

    # Fallback: bounded live scan (only if facets table empty/unbuilt).
    col = _FACET_COLUMNS[kind]
    sql = text(
        f"SELECT v AS value, count(*) AS c FROM ("
        f"  SELECT unnest({col}) AS v FROM stories LIMIT 200000"
        f") s WHERE (:q = '' OR v ILIKE :pat) GROUP BY v ORDER BY c DESC LIMIT :lim"
    )
    rows = db.execute(sql, {"q": ql, "pat": f"%{ql}%", "lim": limit}).fetchall()
    return [{"value": r[0], "count": r[1]} for r in rows]


@router.get("/suggest-canonical")
async def suggest_canonical(
    q: str = Query("", min_length=1),
    kind: str = Query("fandom"),
    limit: int = Query(8, ge=1, le=15),
    db: Session = Depends(get_db),
):
    """Combined fandom/tag autocomplete for the Import tab.

    Index-first, AO3-canonical fallback: suggest fandoms we already have (instant),
    and when the typed text doesn't match the index, query AO3's public autocomplete
    so the user can discover and correctly spell NEW fandoms to scrape. Picking a
    canonical suggestion guarantees valid AO3 tag syntax (avoids the malformed-tag
    URLs that broke earlier discover jobs).

    Returns: [{value, count, source}] where source is 'index' or 'ao3'.
    """
    ql = q.strip().lower()
    out: list[dict] = []
    seen: set[str] = set()

    # 1) Index suggestions (fast, from the facets table)
    try:
        rows = db.execute(text(
            "SELECT value, count FROM facets "
            "WHERE kind = :kind AND value ILIKE :pat "
            "ORDER BY count DESC LIMIT :lim"
        ), {"kind": kind if kind in _FACET_COLUMNS else "fandom",
            "pat": f"%{ql}%", "lim": limit}).fetchall()
        for r in rows:
            key = r[0].lower()
            if key not in seen:
                seen.add(key)
                out.append({"value": r[0], "count": r[1], "source": "index"})
    except Exception:
        pass

    # 2) If we have few index hits, top up with AO3 canonical tags
    if len(out) < limit:
        try:
            import httpx
            from live_fetch.ao3_feeds import HEADERS, AO3_LIVE_TIMEOUT
            # AO3's public autocomplete endpoint, e.g.
            # /autocomplete/fandom?term=harry  → [{id, name}, ...]
            ao3_kind = {"fandom": "fandom", "relationship": "relationship",
                        "character": "character", "tag": "freeform"}.get(kind, "fandom")
            url = f"https://archiveofourown.org/autocomplete/{ao3_kind}?term={ql}"
            async with httpx.AsyncClient(headers=HEADERS, timeout=AO3_LIVE_TIMEOUT,
                                         follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    for item in r.json():
                        name = (item.get("name") or "").strip()
                        if name and name.lower() not in seen:
                            seen.add(name.lower())
                            out.append({"value": name, "count": None, "source": "ao3"})
                        if len(out) >= limit:
                            break
        except Exception:
            # AO3 slow/unreachable — index suggestions are still useful on their own
            pass

    return out[:limit]


@router.post("/refresh-facets")
async def refresh_facets(db: Session = Depends(get_db)):
    """Rebuild the facets table from current stories. Run this after big imports
    so autocomplete reflects the latest data. Takes a while on millions of rows,
    but it's a one-shot batch, not per-request."""
    built = {}
    for kind, col in _FACET_COLUMNS.items():
        db.execute(text("DELETE FROM facets WHERE kind = :k"), {"k": kind})
        db.execute(text(
            f"INSERT INTO facets (kind, value, count) "
            f"SELECT :k, v, count(*) FROM ("
            f"  SELECT unnest({col}) AS v FROM stories"
            f") s WHERE v IS NOT NULL AND v <> '' "
            f"GROUP BY v"
        ), {"k": kind})
        n = db.execute(text("SELECT count(*) FROM facets WHERE kind = :k"), {"k": kind}).scalar()
        built[kind] = n
    db.commit()
    return {"ok": True, "facets": built}
