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
  off. The badge stays generous; mail is held to the stricter test.
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
        SELECT fe.user_id::text, u.email, u.username, fe.token
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
                OR s.updated_at > f.seen_updated)
         ORDER BY s.updated_at DESC NULLS LAST
    """), {"u": user_id}).fetchall()


def compose(username: str, works: list, token: str) -> tuple[str, str, dict]:
    site = mailer.SITE_URL or "https://ficatlas.com"
    n = len(works)
    subject = (f"New chapter{'s' if works[0][5] > 1 else ''}: {works[0][1]}" if n == 1
               else f"New chapters in {n} stories you follow")
    lines = [f"Hi {username},", "",
             ("A story you follow has a new chapter:" if n == 1 and works[0][5] == 1 else
              "A story you follow has new chapters:" if n == 1 else
              f"{n} stories you follow have new chapters:"), ""]
    for sid, title, author, s, chapters, new in works[:MAX_LISTED]:
        lines.append(f"  {title}" + (f" by {author}" if author else ""))
        lines.append(f"  {new} new chapter{'s' if new != 1 else ''} (now {chapters})"
                     f" · {SITE_LABELS.get(s, s)}")
        lines.append(f"  {site}/story/{sid}")
        lines.append("")
    if n > MAX_LISTED:
        lines += [f"…and {n - MAX_LISTED} more.", ""]
    unsub_page = f"{site}/unsubscribe#t={token}"
    lines += [
        "Everything you follow, across every archive, in one list:",
        f"  {site}/library?tab=following",
        "",
        "You are getting this because you turned on update emails at",
        "FicAtlas. To stop them, open this link, or untick the box on your",
        "Following tab:",
        f"  {unsub_page}",
        "",
        "-- ",
        "FicAtlas",
        "Search AO3, FanFiction.net and FictionAlley at once",
        site,
    ]
    headers = {
        # RFC 8058 one-click: mail clients show their own Unsubscribe button and
        # POST here without opening a page. Gmail and Yahoo require it of
        # anyone sending in volume, and it is what keeps a digest out of spam.
        "List-Unsubscribe": f"<{site}/api/follows/email/unsubscribe?t={token}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return subject, "\n".join(lines) + "\n", headers


def run(db, send=None) -> dict:
    """One pass: mail every opted-in reader who is due and has something new."""
    send = send or mailer.send
    stats = {"due": 0, "sent": 0, "failed": 0, "works": 0}
    if send is mailer.send and not mailer.configured():
        return stats
    for user_id, email, username, token in _due(db):
        stats["due"] += 1
        works = moved(db, user_id)
        if not works:
            continue
        subject, body, headers = compose(username, works, token)
        if not send(email, subject, body, headers):
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
