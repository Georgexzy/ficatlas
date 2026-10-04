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
    RETURNING site_id, line
""")


def apply_batch(limit: int = 2000) -> dict:
    """Claim up to `limit` staged rows, parse them, write the ones we hold.

    Claiming DELETEs, so a row is applied at most once; a crash mid-batch loses
    those rows until the harvester walks the page again, which it does on every
    lap — the safe direction, since this only ever adds information.
    """
    with db_session() as db:
        lift_statement_timeout(db)
        rows = db.execute(CLAIM_SQL, {"n": limit}).fetchall()
        db.commit()
        if not rows:
            return {"claimed": 0, "parsed": 0, "matched": 0, "written": 0, "with_characters": 0}
        ids = [str(r[0]) for r in rows]
        found = dict(db.execute(text(
            "SELECT site_id, id FROM stories WHERE site = 'ffnet' AND site_id = ANY(:ids)"),
            {"ids": ids}).fetchall())

    items, parsed, with_chars = [], 0, 0
    for site_id, line in rows:
        # A live listing page: a no-year date means this year.
        meta = parse_ffn_meta(line, listing=True, ref=datetime.now(timezone.utc))
        if not meta:
            continue
        parsed += 1
        sid = found.get(str(site_id))
        if sid is None:
            continue
        if meta.get("characters"):
            with_chars += 1
        items.append((sid, meta))

    written = _write_batch(items) if items else 0
    return {"claimed": len(rows), "parsed": parsed, "matched": len(items),
            "written": written, "with_characters": with_chars}


def record(db, stats: dict) -> None:
    """Evidence for the admin panel: when rows were last applied, and how many."""
    from api.settings import put_setting
    import time
    put_setting(db, "ffnet_listing_applied_at", str(int(time.time())))
    put_setting(db, "ffnet_listing_applied_last", str(stats.get("written", 0)))
