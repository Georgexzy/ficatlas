"""Mail a reader when a work they follow gains chapters — only if they asked.

Following already works without mail: the Following tab and the header badge
compare each follow with the story row at read time (see api/follows.py). That
only helps a reader who comes back. This is the half that brings them back:
an archive emails its own subscribers, and somebody following works on three
archives here had no way to be told about any of them.

Shape
-----
* OPT-IN. A `follow_email` row is the reader ticking the box; unsubscribing
  deletes it. Nobody is mailed by default, and an account with no address is
  never mailed whatever the row says.
* A DIGEST, never one mail per chapter. `daily` sends at most one mail in any
  23 hours, `hourly` at most one in 50 minutes, each listing every followed
  work that has moved. A reader following thirty WIPs gets one message.
* Announced ONCE. `follows.mailed_chapters` records what was last mailed, and a
  work qualifies only when it has more chapters than the greater of what the
  reader SAW and what they were MAILED. One new chapter sitting unread for a
  week is mentioned in the first digest and not in the six after it.
* The archive's own date must have moved as well, when we hold both dates. A
  chapter count can rise without the work changing — a metadata correction, a
  listing that finally reports a count the bulk dump got wrong — and mailing a
  reader "new chapter!" about a repair is how a feature like this gets turned
  off. The badge stays generous; mail is held to the stricter test. That date
  is day-granular, so a second chapter on the SAME day as the first could not
  move it and went unmailed; a re-read of the work page that found more
  chapters (chapters_bumped_at) satisfies the test instead, at wall-clock
  precision, and only for the paths that read the work page itself.
* Links go to the story page HERE, not to the archive. The point is the
  return visit, and the story page is one click from the archive anyway.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text

import mailer

log = logging.getLogger(__name__)

GAP = {"hourly": "50 minutes", "daily": "23 hours"}
MAX_PER_RUN = 200        # mail provider limits are per day; this is a backstop
MAX_LISTED = 25          # works named in one digest; the rest are a count

SITE_LABELS = {"ao3": "AO3", "ffnet": "FanFiction.net", "fictionalley": "FictionAlley"}


def _due(db) -> list:
    return db.execute(text(f"""
        SELECT fe.user_id::text, u.email, u.username, fe.token, fe.frequency
          FROM follow_email fe JOIN users u ON u.id = fe.user_id
         WHERE coalesce(u.email, '') <> ''
           AND (fe.last_sent_at IS NULL OR fe.last_sent_at < now() - CASE fe.frequency
                    WHEN 'hourly' THEN interval '{GAP["hourly"]}'
                    ELSE interval '{GAP["daily"]}' END)
         ORDER BY fe.last_sent_at NULLS FIRST
         LIMIT :n
    """), {"n": MAX_PER_RUN}).fetchall()


def moved(db, user_id: str) -> list:
    """Followed works with chapters this reader has neither seen nor been mailed."""
    return db.execute(text("""
        SELECT s.id::text, s.title, s.author, s.site::text, s.chapter_count,
               s.chapter_count - GREATEST(f.seen_chapters, coalesce(f.mailed_chapters, 0)) AS new
          FROM follows f JOIN stories s ON s.id = f.story_id
         WHERE f.user_id = :u
           AND s.delisted_at IS NULL
           AND s.chapter_count > GREATEST(f.seen_chapters, coalesce(f.mailed_chapters, 0))
           AND (s.updated_at IS NULL OR f.seen_updated IS NULL
                OR s.updated_at > f.seen_updated
                -- The archive's own date is DAY-granular, so a second chapter
                -- on the same day as the first cannot move it: "Mental
                -- Gymnastics" (54) and "Intimate Relations" (55) were both
                -- 2026-10-14, and the reader who marked the first seen never
                -- got mail about the second. chapters_bumped_at is the same
                -- claim at wall-clock precision, set when a re-read of the
                -- WORK PAGE found more chapters than we held.
                --
                -- Pinned to the work's own date as well, because
                -- apply_work also serves the title-repair backfill, where a
                -- dump reporting 12 for a work that really has 40 is a
                -- CORRECTION and not news — the count rises while the work's
                -- date stays years old. A real new chapter moves both: the
                -- archive dates it the day it posted, so within a few days of
                -- our having seen the count rise. Outside that window the old
                -- date test decides, exactly as before.
                OR (s.chapters_bumped_at IS NOT NULL
                    AND s.updated_at IS NOT NULL
                    AND s.updated_at >= s.chapters_bumped_at - interval '3 days'))
         ORDER BY s.updated_at DESC NULLS LAST
    """), {"u": user_id}).fetchall()


def compose(username: str, works: list, token: str, frequency: str = "daily",
            test: bool = False):
    """(subject, text, headers, html). The layout and copy are in email_templates."""
    import email_templates
    subject, text_body, html_body, headers = email_templates.digest_email(
        username=username, works=works, token=token, frequency=frequency,
        max_listed=MAX_LISTED, test=test)
    return subject, text_body, headers, html_body


def run(db, send=None) -> dict:
    """One pass: mail every opted-in reader who is due and has something new."""
    send = send or mailer.send
    stats = {"due": 0, "sent": 0, "failed": 0, "works": 0}
    if send is mailer.send and not mailer.configured():
        return stats
    for user_id, email, username, token, frequency in _due(db):
        stats["due"] += 1
        works = moved(db, user_id)
        if not works:
            continue
        subject, body, headers, html_body = compose(username, works, token, frequency)
        if not send(email, subject, body, headers, html=html_body):
            stats["failed"] += 1
            continue
        stats["sent"] += 1
        stats["works"] += len(works)
        db.execute(text("""
            UPDATE follows f SET mailed_chapters = s.chapter_count
              FROM stories s
             WHERE s.id = f.story_id AND f.user_id = :u
               AND f.story_id = ANY(CAST(:ids AS uuid[]))
        """), {"u": user_id, "ids": [w[0] for w in works]})
        db.execute(text("UPDATE follow_email SET last_sent_at = now() WHERE user_id = :u"),
                   {"u": user_id})
        db.commit()
    return stats


def record(db, stats: dict) -> None:
    """Evidence for the admin panel — a pass that ran, and what it sent."""
    from api.settings import put_setting
    put_setting(db, "follow_email_ran_at", str(int(time.time())))
    if stats.get("sent"):
        put_setting(db, "follow_email_sent_at", str(int(time.time())))
    put_setting(db, "follow_email_last", str(stats.get("sent", 0)))
