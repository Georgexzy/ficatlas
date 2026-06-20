"""Runtime settings — small key/value store for user-configurable options."""
from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session
from sqlalchemy import text
from db.session import get_db

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
}


def _ensure_table(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """))
    db.commit()


def get_setting(db: Session, key: str) -> str:
    _ensure_table(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key=:k"), {"k": key}).first()
    return row[0] if row else DEFAULTS.get(key, "")


@router.get("")
async def all_settings(db: Session = Depends(get_db)):
    _ensure_table(db)
    rows = db.execute(text("SELECT key, value FROM app_settings")).fetchall()
    stored = {r[0]: r[1] for r in rows}
    return {**DEFAULTS, **stored}


@router.post("")
async def set_setting(key: str = Form(...), value: str = Form(...), db: Session = Depends(get_db)):
    _ensure_table(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = :v
    """), {"k": key, "v": value})
    db.commit()
    return {"ok": True, "key": key, "value": value}
