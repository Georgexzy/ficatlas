"""Runtime settings — small key/value store for user-configurable options."""
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from db.session import get_db
from api.auth import require_admin

router = APIRouter()

DEFAULTS = {
    "tracked_fandom":      "Harry Potter - J. K. Rowling",
    "poll_on_load":        "true",
    "default_sites":       "ao3,ffnet,fictionalley",
    "default_sort":        "relevance",
    "results_per_page":    "20",
    "reader_font":         "serif",
    "reader_width":        "narrow",
    "show_explicit":       "false",
    "live_fetch":          "true",
    # Feed-poll filters — post-filter the 25 newest works per tag
    "feed_min_words":      "",        # blank = no min
    "feed_max_words":      "",        # blank = no max
    "feed_complete_only":  "false",
    # Direct site crawling (AO3/FFN). OFF by default and rarely works from a
    # datacenter IP — AO3 returns Cloudflare 525s and FFN is fully walled. The
    # feed poller above is the reliable freshness path; this is for users on a
    # residential IP / Tailscale exit node / WARP who can actually reach the sites.
    "enable_direct_crawl": "false",
    # Per-site circuit breaker. When a site's scheduled crawl fails repeatedly
    # (see CRAWL_FAIL_* env in scheduler.py), it's auto-disabled by flipping the
    # matching flag below to "true". Blank/"false" = the site may crawl normally.
    # Reset to "" in Settings to re-enable after fixing connectivity.
    "crawl_disabled_ao3":   "",
    "crawl_disabled_ffnet": "",
}


# The table only has to be created once per process, but _ensure_table ran a
# CREATE TABLE IF NOT EXISTS plus a COMMIT on every single get/put — and
# all_settings is called on page load. Do it once and remember.
_table_ready = False


def _ensure_table(db: Session):
    global _table_ready
    if _table_ready:
        return
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """))
    db.commit()
    _table_ready = True


def get_setting(db: Session, key: str) -> str:
    _ensure_table(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key=:k"), {"k": key}).first()
    return row[0] if row else DEFAULTS.get(key, "")


def put_setting(db: Session, key: str, value: str) -> None:
    """Upsert a setting from server-side code (used by the scheduler's crawler
    circuit breaker). Mirrors the POST endpoint's write."""
    _ensure_table(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = :v
    """), {"k": key, "v": str(value)})
    db.commit()


@router.get("")
async def all_settings(db: Session = Depends(get_db)):
    _ensure_table(db)
    rows = db.execute(text("SELECT key, value FROM app_settings")).fetchall()
    stored = {r[0]: r[1] for r in rows}
    return {**DEFAULTS, **stored}


@router.post("")
async def set_setting(
    key: str = Form(...),
    value: str = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Write one setting.

    These are INSTANCE-wide, not per-user — they include things like
    enable_direct_crawl and the tracked fandom — so this needs the same guard as
    the library admin endpoints: any signed-in user, or anyone at all while the
    instance still has no accounts.

    Keys are restricted to the known set. The endpoint previously accepted any
    key at all, so app_settings was an unbounded write-anything key/value store.
    """
    if key not in DEFAULTS:
        raise HTTPException(400, f"Unknown setting '{key}'")
    if len(value) > 2000:
        raise HTTPException(400, "Setting value too long")

    _ensure_table(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = :v
    """), {"k": key, "v": value})
    db.commit()
    return {"ok": True, "key": key, "value": value}
