"""Stats endpoint — per-site counts, totals, last-updated info"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import bindparam, func, text
from db.session import get_db
from models.story import Story
from provenance import PROVENANCE_TAGS
from api.auth import require_admin

log = logging.getLogger(__name__)

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
def site_stats(
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

# Where the last computed totals are kept between restarts.
#
# Stale-while-revalidate only helps once a value EXISTS. In memory it does not
# survive a restart, so the first visitor after every deploy paid the full scan
# inline — measured at 10s on a quiet index and 17-45s while the background jobs
# are running, which is past the point where a browser gives up. Every page load
# calls this, so a restart briefly made the whole site look broken.
#
# Persisting the numbers turns that into: serve last known figures instantly,
# recompute behind the response. They are counts of a 19.7M-row index shown in a
# status widget — being a few minutes stale is invisible, and being absent is not.
_TOTALS_SETTING = "cached_totals_v1"


def _persist_totals(payload: dict) -> None:
    try:
        from db.session import db_session
        from api.settings import put_setting
        import json
        with db_session() as db:
            put_setting(db, _TOTALS_SETTING, json.dumps(payload))
    except Exception:
        pass  # a cache that fails to persist is slow later, not broken now


def _load_persisted_totals() -> dict | None:
    try:
        from db.session import db_session
        from api.settings import get_setting
        import json
        with db_session() as db:
            raw = get_setting(db, _TOTALS_SETTING)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _recompute_totals() -> None:
    """Refresh the cached totals off the request path."""
    global _totals_cache, _totals_cached_at
    import time
    from db.session import db_session
    try:
        with db_session() as db:
            row = db.execute(_TOTALS_SQL).mappings().first()
            coverage = _compute_coverage(db)
        _totals_cache = {
            "stories": row["stories"], "hosted": row["hosted"],
            "total_words": int(row["total_words"]), "dlp": row["dlp"],
            "hpffa": row["hpffa"],
            "indexed_last_hour": row["indexed_last_hour"],
            "indexed_last_day": row["indexed_last_day"],
            "coverage": coverage,
        }
        _totals_cached_at = time.monotonic()
        _persist_totals(_totals_cache)
    except Exception:
        pass  # keep serving the previous numbers


# Field coverage, sampled. Served publicly because the search UI explains its
# own filters with these numbers, and a hard-coded explanation goes stale
# silently: the ship/character help claimed FictionAlley was "18% ships, 82%
# characters" while the live figures were 14% and 94%. A number written into
# prose is a number nobody updates.
_COVERAGE_SQL = text("""
    SELECT site,
           count(*)                                                      AS n,
           count(*) FILTER (WHERE relationships <> '{}' AND relationships IS NOT NULL) AS ships,
           count(*) FILTER (WHERE characters    <> '{}' AND characters    IS NOT NULL) AS chars
    FROM stories TABLESAMPLE SYSTEM_ROWS(60000)
    GROUP BY site
""")


def _compute_coverage(db) -> dict:
    try:
        rows = db.execute(_COVERAGE_SQL).mappings().all()
    except Exception:
        return {}
    out = {}
    for r in rows:
        n = r["n"] or 0
        site = r["site"].value if hasattr(r["site"], "value") else str(r["site"])
        # A block sample gives a small site a handful of rows — FictionAlley is
        # 0.15% of the index, so 60,000 sampled rows contain about ninety of it,
        # and a percentage from ninety rows swings by double digits run to run.
        # Small sites are counted exactly instead; at 30k rows it is a fast
        # index scan, and the alternative is quoting a number that is wrong.
        if n < 500:
            continue
        out[site] = {"ships": round(100 * r["ships"] / n),
                     "characters": round(100 * r["chars"] / n)}

    for site in ("fictionalley",):
        if site in out:
            continue
        try:
            e = db.execute(text("""
                SELECT count(*) AS n,
                       count(*) FILTER (WHERE relationships <> '{}' AND relationships IS NOT NULL) AS ships,
                       count(*) FILTER (WHERE characters    <> '{}' AND characters    IS NOT NULL) AS chars
                FROM stories WHERE site = :s
            """), {"s": site}).mappings().first()
            if e and e["n"]:
                out[site] = {"ships": round(100 * e["ships"] / e["n"]),
                             "characters": round(100 * e["chars"] / e["n"])}
        except Exception as e:
            # Best-effort: a site without an exact count is simply omitted, and
            # the caller renders what it has. Logged because a `pass` that never
            # says anything turns a permanent failure into a silent one.
            log.debug(f"exact coverage for {site} failed: {type(e).__name__}")
    return out


_TOTALS_SQL = text("""
    SELECT count(*)                                                   AS stories,
           count(*) FILTER (WHERE is_hosted)                          AS hosted,
           coalesce(sum(word_count), 0)                               AS total_words,
           count(*) FILTER (WHERE tags @> ARRAY['dlp_library'])       AS dlp,
           count(*) FILTER (WHERE tags @> ARRAY['hpffa_archive'])     AS hpffa,
           -- Folded into this scan rather than queried separately: there is no
           -- index on indexed_at, so on its own it would be another full pass
           -- over 19.6M rows. Here it costs nothing, at the price of being as
           -- fresh as the 5-minute cache — fine for "in the last hour".
           count(*) FILTER (WHERE indexed_at > now() - interval '1 hour')  AS indexed_last_hour,
           count(*) FILTER (WHERE indexed_at > now() - interval '24 hours') AS indexed_last_day
    FROM stories
""")


@router.get("/totals")
def total_stats(
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    global _totals_cache, _totals_cached_at
    import time
    now = time.monotonic()

    # Cold process: adopt the numbers from before the restart so this request
    # can be answered now, and let the recompute happen behind it.
    if _totals_cache is None and not refresh:
        stored = _load_persisted_totals()
        if stored:
            _totals_cache = stored
            # Deliberately backdated past the TTL so it counts as stale and a
            # refresh is scheduled immediately — this is a starting point, not
            # a fresh reading.
            _totals_cached_at = now - _TOTALS_TTL_SECONDS - 1

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
        "indexed_last_hour": row["indexed_last_hour"],
        "indexed_last_day": row["indexed_last_day"],
    }
    _totals_cached_at = now
    _persist_totals(_totals_cache)
    return _totals_cache


@router.get("/suggest")
def suggest(
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

    # Try the precomputed facet table first. Provenance is filtered at read time as
    # well as at rebuild time, so a facets table built before this rule existed
    # still won't suggest import tags.
    prov = sorted(PROVENANCE_TAGS)
    try:
        if ql:
            rows = db.execute(text(
                "SELECT value, count FROM facets "
                "WHERE kind = :kind AND value ILIKE :pat AND NOT (value = ANY(:provenance)) "
                "ORDER BY count DESC LIMIT :lim"
            ), {"kind": kind, "pat": f"%{ql}%", "lim": limit, "provenance": prov}).fetchall()
        else:
            rows = db.execute(text(
                "SELECT value, count FROM facets "
                "WHERE kind = :kind AND NOT (value = ANY(:provenance)) "
                "ORDER BY count DESC LIMIT :lim"
            ), {"kind": kind, "lim": limit, "provenance": prov}).fetchall()
        if rows:
            return [{"value": r[0], "count": r[1]} for r in rows]
    except Exception as e:
        log.debug(f"suggest: index lookup failed ({type(e).__name__}); falling back")

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
        f") s WHERE (:q = '' OR v ILIKE :pat) AND NOT (v = ANY(:provenance)) "
        f"GROUP BY v ORDER BY c DESC LIMIT :lim"
    )
    rows = db.execute(sql, {"q": ql, "pat": f"%{ql}%", "lim": limit,
                            "provenance": sorted(PROVENANCE_TAGS)}).fetchall()
    return [{"value": r[0], "count": r[1]} for r in rows]


@router.get("/suggest-canonical")
def suggest_canonical(
    q: str = Query("", min_length=1),
    kind: str = Query("fandom"),
    limit: int = Query(8, ge=1, le=15),
    db: Session = Depends(get_db),
):
    """Combined fandom/tag autocomplete for the Import tab.

    Index first, then AO3's canonical vocabulary for fandoms we do not hold —
    which is the case that matters here, since this box exists to find something
    new to scrape. Both come from our own facets table; see
    ao3_canonical_fandoms.py for how the canonical half gets there without
    hitting the /autocomplete/ endpoint AO3 disallows.

    Returns: [{value, count, source}]. 'index' means works we hold and the count
    is ours; 'ao3' means a canonical fandom we have not indexed and the count is
    AO3's.
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
    except Exception as e:
        log.debug(f"suggest: index lookup failed ({type(e).__name__}); falling back")

    # 2) Top up with AO3's canonical fandom names for anything we do not hold.
    #
    # This used to proxy AO3's /autocomplete/ per keystroke, which their
    # robots.txt disallows outright. The vocabulary now comes from our own
    # facets table, synced occasionally by ao3_canonical_fandoms.py from the
    # eleven /media/<category>/fandoms pages — not disallowed, and eleven
    # requests total rather than one per character typed.
    #
    # The top-up matters most exactly where the index cannot help: this box
    # exists to find a fandom to START scraping, so not having it yet is the
    # normal case, and a canonical name is what makes the resulting tag URL
    # valid.
    if kind == "fandom" and len(out) < limit:
        try:
            rows = db.execute(text(
                "SELECT value, count FROM facets "
                "WHERE kind = 'fandom_ao3' AND value ILIKE :pat "
                "ORDER BY count DESC LIMIT :lim"
            ), {"pat": f"%{ql}%", "lim": limit}).fetchall()
            for r in rows:
                if r[0].lower() in seen:
                    continue
                seen.add(r[0].lower())
                out.append({"value": r[0], "count": r[1], "source": "ao3"})
                if len(out) >= limit:
                    break
        except Exception as e:
            log.debug(f"suggest: source unavailable ({type(e).__name__})")

    return out[:limit]


@router.post("/refresh-facets")
def refresh_facets(
    min_count: int = Query(1, ge=1, description="Drop values rarer than this"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
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
            # Provenance tags ("ffnet_dump", "ao3_meta_dump", …) live in the same
            # array as content tags but describe which import a row came from, not
            # what it is about. With millions of uses each they dominated tag
            # autocomplete — typing "dump" suggested ffnet_dump (3.4M) ahead of the
            # real tag "Infodumping" (9). Keep them filterable, but never suggest
            # them as content tags.
            db.execute(text(
                f"INSERT INTO facets_rebuild (kind, value, count) "
                f"SELECT :k, v, count(*) AS c FROM ("
                f"  SELECT unnest({col}) AS v FROM stories"
                f") s WHERE v IS NOT NULL AND v <> '' AND NOT (v = ANY(:provenance)) "
                f"GROUP BY v HAVING count(*) >= :min_count"
            ), {"k": kind, "min_count": min_count,
                "provenance": sorted(PROVENANCE_TAGS)})
            built[kind] = db.execute(
                text("SELECT count(*) FROM facets_rebuild WHERE kind = :k"), {"k": kind}
            ).scalar()

        # Carry over kinds that are NOT derived from stories. The rebuild below
        # swaps a freshly-built table in and drops the old one, so anything this
        # loop did not produce would be silently destroyed — and the AO3
        # canonical vocabulary costs eleven network requests to regenerate,
        # which a local facets rebuild has no business triggering.
        carried = db.execute(text(
            "INSERT INTO facets_rebuild (kind, value, count) "
            "SELECT kind, value, count FROM facets WHERE kind NOT IN :kinds "
            "ON CONFLICT (kind, value) DO NOTHING"
        ).bindparams(bindparam("kinds", expanding=True)),
            {"kinds": list(_FACET_COLUMNS.keys())}).rowcount
        if carried:
            built["carried_over"] = carried

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
