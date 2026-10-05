"""Keep FOLLOWED works current, rather than leaving them to chance.

Following compares the story row with what the reader last saw (api/follows.py)
and the update mail does the same (follow_email.py). Both are only as fresh as
the row, and nothing refreshed a row BECAUSE somebody followed it. Measured
2026-10-05: an AO3 update was picked up the same day 47% of the time and an
FF.net one 0% (19% by the next day) — and those are the updates we caught at
all. A WIP in a fandom outside the crawl rotation waited for the stale-WIP
refresh, which only revisits a work after seven days.

Two routes, one per archive, because the archives allow different things:

* AO3 — re-read the work page, the same request the stale-WIP refresh makes,
  through the same process-wide `ao3_budget`. A followed work not looked at
  for FOLLOW_REFRESH_HOURS is due. The number of followed works is the cost,
  and it is small; MAX_PER_PASS bounds it if that ever changes.
* FF.net — refuses this server's story pages, but the listing harvester reads
  fandom listings. This publishes the SECTIONS holding followed works to
  `ffnet_followed_sections`, and the harvester reads the first pages of each,
  sorted by update date, at the start of every pass. A section's newest
  updates are on its first page, so that is where a followed work's new
  chapter appears.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone

from sqlalchemy import text

log = logging.getLogger(__name__)

FOLLOW_REFRESH_HOURS = 3
MAX_PER_PASS = 60
MAX_SECTIONS = 12


def due_ao3(db, hours: float = FOLLOW_REFRESH_HOURS, limit: int = MAX_PER_PASS) -> list:
    """Followed AO3 works not looked at for `hours`, least recently checked first.

    Complete works are skipped: a finished work almost never gains a chapter,
    and the stale-WIP refresh still visits them eventually.
    """
    return db.execute(text("""
        SELECT s.id, s.site_id
          FROM stories s
         WHERE s.id IN (SELECT DISTINCT story_id FROM follows)
           AND s.site = 'ao3' AND s.site_id ~ '^[0-9]+$'
           AND s.delisted_at IS NULL AND s.source_restricted_at IS NULL
           AND s.status IS DISTINCT FROM 'complete'
           AND (s.crawled_at IS NULL
                OR s.crawled_at < now() - make_interval(hours => :h))
         ORDER BY s.crawled_at NULLS FIRST
         LIMIT :n
    """), {"h": hours, "n": limit}).fetchall()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def section_for(fandom: str, sections: list) -> str | None:
    """The FF.net listing path for one of our FF.net fandom names, or None.

    Matched on letters and digits only: our names come from the same listings
    ("Harry Potter"), the path spells them with hyphens and percent-escapes
    ("/book/Harry-Potter/"). 289 of 291 sampled live fandoms resolve.
    """
    want = _key(fandom)
    for path, *_ in sections:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and _key(urllib.parse.unquote(parts[1]).replace("-", " ")) == want:
            return path
    return None


def followed_ffnet_sections(db) -> list[str]:
    raw = db.execute(text(
        "SELECT value FROM app_settings WHERE key = 'ffnet_sections'")).scalar()
    try:
        sections = json.loads(raw)["sections"] if raw else []
    except (ValueError, KeyError, TypeError):
        sections = []
    rows = db.execute(text("""
        SELECT f, count(*) FROM (
            SELECT unnest(s.fandoms) AS f FROM stories s
             WHERE s.id IN (SELECT DISTINCT story_id FROM follows)
               AND s.site = 'ffnet' AND s.delisted_at IS NULL
               AND s.status IS DISTINCT FROM 'complete') x
         GROUP BY f ORDER BY count(*) DESC
    """)).fetchall()
    out: list[str] = []
    for fandom, _ in rows:
        p = section_for(fandom or "", sections)
        if p and p not in out:
            out.append(p)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def publish_ffnet(db) -> int:
    from api.settings import put_setting
    secs = followed_ffnet_sections(db)
    put_setting(db, "ffnet_followed_sections", json.dumps(secs))
    return len(secs)


def refresh_ao3(rows: list, delay: float = 2.0) -> dict:
    """Re-read each work page and apply what changed. Network-bound; call in a thread."""
    import httpx
    from ao3_title_repair import (RESTRICTED, RateLimiter, THROTTLED,
                                  apply_work, fetch_work, UA)
    from db.session import db_session
    from models.story import Story

    limiter = RateLimiter(delay)
    stats = {"checked": 0, "changed": 0, "chapters": 0, "restricted": 0}
    with httpx.Client(headers=UA) as client:
        for sid, work_id in rows:
            limiter.wait()
            data = fetch_work(client, work_id, limiter)
            if data is THROTTLED:
                break          # the shared budget has backed off; try next pass
            with db_session() as db:
                if data is RESTRICTED:
                    db.execute(text("UPDATE stories SET source_restricted_at = "
                                    "COALESCE(source_restricted_at, now()) WHERE id = :s"),
                               {"s": sid})
                    db.commit()
                    stats["restricted"] += 1
                    continue
                story = db.query(Story).filter(Story.id == sid).first()
                if not story:
                    continue
                if data:
                    fields = apply_work(story, data)
                    stats["checked"] += 1
                    stats["changed"] += bool(fields)
                    stats["chapters"] += "chapter_count" in fields
                # Stamped even when the page said nothing new, or the same work
                # is due again on the very next pass.
                story.crawled_at = datetime.now(timezone.utc)
                db.commit()
    return stats


def record(db, stats: dict) -> None:
    from api.settings import put_setting
    put_setting(db, "follow_refresh_ran_at", str(int(time.time())))
    put_setting(db, "follow_refresh_last", json.dumps(stats))
