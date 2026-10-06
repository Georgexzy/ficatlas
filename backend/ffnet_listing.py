"""Apply FF.net listing rows staged by the browser harvester.

FanFiction.net refuses this server's direct requests for story pages, so the
index has filled FF.net characters one archived snapshot at a time — about 1,800
fetches a day from archive.org, of which roughly 500 come back carrying
characters. Against 6.47M FF.net works with none, that is decades.

Meanwhile the browser harvester (ffnet-harvester/harvest.js) was reading
246,861 FF.net listing rows a day. Each row carries the same metadata line a
story page does — characters, bracketed pairings, genres, words, completion and
dates — and the harvester kept three counts out of it and dropped the rest.

It now stages the raw line in `ffnet_listing_meta`, and this applies it:
parsed by the one FF.net parser (`parse_ffn_meta`, with `listing=True` for the
row's field order) and written by the one FF.net writer (`_write_batch`), so a
listing and an archived page can never disagree about what a field means.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from db.session import db_session, lift_statement_timeout
from ffnet_enrich import _write_batch, parse_ffn_meta

log = logging.getLogger(__name__)

CLAIM_SQL = text("""
    DELETE FROM ffnet_listing_meta
     WHERE site_id IN (SELECT site_id FROM ffnet_listing_meta
                        ORDER BY seen_at LIMIT :n FOR UPDATE SKIP LOCKED)
    RETURNING site_id, line, title, author, author_url, summary, fandom, seen_at
""")


def _new_work(site_id, meta: dict, title: str, author: str, author_url: str | None,
              summary: str | None, fandom: str | None) -> dict:
    """A listing row as the shared save routine's input, for a work we lack."""
    complete = meta.get("complete")
    iso = lambda d: d.isoformat() if d else None
    return {
        "site": "ffnet", "site_id": str(site_id),
        "url": f"https://www.fanfiction.net/s/{site_id}/1/",
        "title": title, "author": author, "author_url": author_url,
        "summary": summary,
        "fandoms": [fandom] if fandom else [],
        "characters": meta.get("characters") or [],
        "relationships": meta.get("relationships") or [],
        "genres": meta.get("genres") or [],
        # FF.net genres are its content tags, as _write_batch treats them.
        "tags": list(meta.get("genres") or []),
        "rating": meta.get("rating"),
        "language": meta.get("language") or "English",
        "word_count": meta.get("words"),
        "chapter_count": meta.get("chapters") or 1,
        "kudos": meta.get("favs"), "comments": meta.get("reviews"),
        "bookmarks": meta.get("follows"),
        "status": ("complete" if complete else "in_progress" if complete is False else "unknown"),
        "published_at": iso(meta.get("published_at")),
        "updated_at": iso(meta.get("updated_at") or meta.get("published_at")),
    }


def apply_batch(limit: int = 2000, add_new: bool | None = None) -> dict:
    """Claim up to `limit` staged rows, parse them, write the ones we hold and
    ADD the ones we do not.

    Claiming DELETEs, so a row is applied at most once; a crash mid-batch loses
    those rows until the harvester walks the page again, which it does on every
    lap — the safe direction, since this only ever adds information.

    About 40% of listing rows are works newer than the bulk dumps the index was
    built from. A row carrying a title and author is enough to index one, and
    it goes through persist_live_results like every other import — so cross-post
    merging, the crossover flag and the content-gate trigger all apply exactly
    as they do to anything else. `add_new` defaults on (FFNET_LISTING_ADD_NEW).
    """
    import os
    from live_fetch.persist import persist_live_results
    if add_new is None:
        add_new = os.getenv("FFNET_LISTING_ADD_NEW", "true").lower() in ("1", "true", "yes")

    with db_session() as db:
        lift_statement_timeout(db)
        rows = db.execute(CLAIM_SQL, {"n": limit}).fetchall()
        db.commit()
        if not rows:
            return {"claimed": 0, "parsed": 0, "matched": 0, "written": 0,
                    "with_characters": 0, "added": 0}
        ids = [str(r[0]) for r in rows]
        found = dict(db.execute(text(
            "SELECT site_id, id FROM stories WHERE site = 'ffnet' AND site_id = ANY(:ids)"),
            {"ids": ids}).fetchall())

    items, fresh, parsed, with_chars = [], [], 0, 0
    now = datetime.now(timezone.utc)
    for site_id, line, title, author, author_url, summary, fandom, seen_at in rows:
        # A live listing page: a no-year date means this year, and "22h ago"
        # means 22 hours before the page was READ — seen_at, not now.
        ref = seen_at if seen_at is not None else now
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        meta = parse_ffn_meta(line, listing=True, ref=ref)
        if not meta:
            continue
        parsed += 1
        sid = found.get(str(site_id))
        if sid is not None:
            if meta.get("characters"):
                with_chars += 1          # among works UPDATED, as logged before
            items.append((sid, meta))
        elif add_new and title and author:
            fresh.append(_new_work(site_id, meta, title, author, author_url, summary, fandom))

    written = _write_batch(items) if items else 0
    added = 0
    if fresh:
        with db_session() as db:
            added = persist_live_results(db, fresh)
    return {"claimed": len(rows), "parsed": parsed, "matched": len(items),
            "written": written, "with_characters": with_chars, "added": added}


def record(db, stats: dict) -> None:
    """Evidence for the admin panel: when rows were last applied, and how many."""
    from api.settings import put_setting
    import time
    put_setting(db, "ffnet_listing_applied_at", str(int(time.time())))
    put_setting(db, "ffnet_listing_applied_last", str(stats.get("written", 0)))
