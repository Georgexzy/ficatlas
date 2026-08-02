"""Stats endpoint — per-site counts, totals, last-updated info"""
from fastapi import APIRouter, BackgroundTasks, Depends, Query
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


# Same story as /totals: this feeds the index status widget on every page load,
# but grouping and taking max(indexed_at) over the whole table can't be answered
# from an index, so each call was a full scan (~1.8s at 4M rows). Cached for the
# same reason — these are informational counts that move slowly.
_SITES_TTL_SECONDS = 300
_sites_cache: list | None = None
_sites_cached_at: float = 0.0


def _compute_sites(db: Session) -> list:
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


def _recompute_sites() -> None:
    global _sites_cache, _sites_cached_at
    import time
    from db.session import db_session
    try:
        with db_session() as db:
            _sites_cache = _compute_sites(db)
        _sites_cached_at = time.monotonic()
    except Exception:
        pass  # keep serving the previous numbers


@router.get("/sites")
async def site_stats(
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    global _sites_cache, _sites_cached_at
    import time
    now = time.monotonic()
    fresh = _sites_cache is not None and (now - _sites_cached_at) < _SITES_TTL_SECONDS
    if not refresh and fresh:
        return _sites_cache

    # Same stale-while-revalidate as /totals: this grouping is a full scan (~9s at
    # 18M rows), so never make a page load wait on it once we have numbers.
    if not refresh and _sites_cache is not None and background_tasks is not None:
        background_tasks.add_task(_recompute_sites)
        return _sites_cache

    _sites_cache = _compute_sites(db)
    _sites_cached_at = now
    return _sites_cache

# The index status widget calls /totals on every page load, but these are
# whole-table aggregates — count(*) and sum(word_count) can't be answered from an
# index, so each call scanned every row. Five separate queries meant several
# passes over the table (~2.3s at 2.3M rows, and growing with every import).
#
# One pass now computes all five figures, and the result is cached briefly. These
# numbers move slowly and are purely informational, so a slightly stale count is
# fine; the page load no longer waits on a table scan.
_TOTALS_TTL_SECONDS = 300
_totals_cache: dict | None = None
_totals_cached_at: float = 0.0

def _recompute_totals() -> None:
    """Refresh the cached totals off the request path."""
    global _totals_cache, _totals_cached_at
    import time
    from db.session import db_session
    try:
        with db_session() as db:
            row = db.execute(_TOTALS_SQL).mappings().first()
        _totals_cache = {
            "stories": row["stories"], "hosted": row["hosted"],
            "total_words": int(row["total_words"]), "dlp": row["dlp"],
            "hpffa": row["hpffa"],
        }
        _totals_cached_at = time.monotonic()
    except Exception:
        pass  # keep serving the previous numbers


_TOTALS_SQL = text("""
    SELECT count(*)                                                   AS stories,
           count(*) FILTER (WHERE is_hosted)                          AS hosted,
           coalesce(sum(word_count), 0)                               AS total_words,
           count(*) FILTER (WHERE tags @> ARRAY['dlp_library'])       AS dlp,
           count(*) FILTER (WHERE tags @> ARRAY['hpffa_archive'])     AS hpffa
    FROM stories
""")


@router.get("/totals")
async def total_stats(
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    global _totals_cache, _totals_cached_at
    import time
    now = time.monotonic()
    fresh = _totals_cache is not None and (now - _totals_cached_at) < _TOTALS_TTL_SECONDS
    if not refresh and fresh:
        return _totals_cache

    # Stale-while-revalidate. The scan is ~10s at 18M rows and grows with the
    # index, so once we have any numbers at all we serve them immediately and
    # recompute behind the response rather than making someone wait for a widget.
    if not refresh and _totals_cache is not None and background_tasks is not None:
        background_tasks.add_task(_recompute_totals)
        return _totals_cache

    row = db.execute(_TOTALS_SQL).mappings().first()
    _totals_cache = {
        "stories": row["stories"], "hosted": row["hosted"],
        "total_words": int(row["total_words"]), "dlp": row["dlp"],
        "hpffa": row["hpffa"],
    }
    _totals_cached_at = now
    return _totals_cache


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

    # Fallback, used only while the facets table is empty (never refreshed, or a
    # rebuild is in flight). This is a SAMPLE, not a survey: the LIMIT applies to
    # unnested values in physical heap order, so both the suggestions and their
    # counts reflect an arbitrary slice of the table rather than the whole index.
    # Good enough to keep autocomplete usable; POST /api/stats/refresh-facets for
    # accurate values.
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
async def refresh_facets(
    min_count: int = Query(1, ge=1, description="Drop values rarer than this"),
    db: Session = Depends(get_db),
):
    """Rebuild the facets table from current stories. Run this after big imports so
    autocomplete reflects the latest data. It's a one-shot batch (four grouped
    scans of the whole table), not a per-request cost.

    Built into a staging table and swapped in at the end. The previous version
    deleted each kind before repopulating it, so for the several minutes the
    rebuild took, autocomplete saw an empty table and silently fell back to the
    sampled live scan — and a failure part-way through left it that way.
    """
    built = {}
    db.execute(text("DROP TABLE IF EXISTS facets_rebuild"))
    db.execute(text(
        "CREATE TABLE facets_rebuild ("
        "  kind VARCHAR(20) NOT NULL, value TEXT NOT NULL,"
        "  count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (kind, value))"
    ))
    try:
        for kind, col in _FACET_COLUMNS.items():
            db.execute(text(
                f"INSERT INTO facets_rebuild (kind, value, count) "
                f"SELECT :k, v, count(*) AS c FROM ("
                f"  SELECT unnest({col}) AS v FROM stories"
                f") s WHERE v IS NOT NULL AND v <> '' "
                f"GROUP BY v HAVING count(*) >= :min_count"
            ), {"k": kind, "min_count": min_count})
            built[kind] = db.execute(
                text("SELECT count(*) FROM facets_rebuild WHERE kind = :k"), {"k": kind}
            ).scalar()

        # Swap. Readers see the old table right up until this commit.
        db.execute(text("DROP TABLE IF EXISTS facets_old"))
        db.execute(text("ALTER TABLE facets RENAME TO facets_old"))
        db.execute(text("ALTER TABLE facets_rebuild RENAME TO facets"))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_facets_kind_value_trgm "
            "ON facets USING gin (value gin_trgm_ops)"
        ))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_facets_kind_count ON facets (kind, count DESC)"
        ))
        db.execute(text("DROP TABLE IF EXISTS facets_old"))
        db.commit()
    except Exception:
        db.rollback()
        db.execute(text("DROP TABLE IF EXISTS facets_rebuild"))
        db.commit()
        raise
    return {"ok": True, "facets": built}
