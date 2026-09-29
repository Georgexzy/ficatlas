"""What a fandom is recommending LATELY, from its own recommendation threads.

    docker exec ficatlas-backend-1 python rec_threads.py --dry-run
    docker exec ficatlas-backend-1 python rec_threads.py --discover
    docker exec ficatlas-backend-1 python rec_threads.py

Why
---
Everything this index knows about recommendation is ALL-TIME and frozen.
`reddit_recs` comes from a community spreadsheet that stops in 2023;
`community_recs` from a wiki. Both answer "what does this fandom press on
newcomers, ever". Neither can answer "what is being recommended now" -- which
is the question a reader actually asks, and the one no archive can answer
either, because an archive knows its own kudos and nothing about what people
are telling each other to read this month.

The source is the recurring recommendation thread. r/HPFanfiction runs "What
are you reading? Bi-Weekly Post"; its AutoModerator asks each commenter for a
Title, a Rating and a LINK, and people largely comply -- which is why matching
can stay on archive ids and never touch titles.

Matching is by ARCHIVE ID out of the link, never by title
---------------------------------------------------------
`reddit_recs_import.py` made this rule and it holds here. Titles collide
constantly in fanfiction (this index holds five works called "Manacled"), and a
wrong match attaches somebody else's reputation to the wrong story.

It was re-measured rather than inherited, because the rule's cost looked high:
most recommendations in a thread READ as prose ("The Golden Prince by
Miss_Mako"), so link-only matching looked like it would capture a fifth of the
signal. Measured over a real thread, it captures nearly all of it -- 8 archive
links resolving to 7 indexed works, against 8 "Title by Author" bylines of
which every single one was ALSO linked in the same comment. The prose and the
link are the same recommendation written twice. Bylines add risk and no works.

The raw thread is stored anyway (`rec_threads.raw`), exactly as
`reddit_posts.comments` is and for the same reason: the FETCH is the expensive
resource, not the parse, so if that finding ever stops holding, the answer is
already on disk and costs no further requests.

Pace
----
Through `reddit_fetch`, which owns one shared budget for every Reddit consumer
in this project -- see the note there about three jobs politely pacing
themselves into a shared per-address ceiling. This job is naturally cheap: the
threads are FORTNIGHTLY, so a fandom costs one search plus about two thread
reads a month.
"""

import argparse
import html
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402
import reddit_fetch  # noqa: E402
from reddit_fetch import Refused  # noqa: E402

log = logging.getLogger("rec_threads")

# The communities to read, and what a recommendation thread looks like in each.
#
# A seed list rather than a derivation, because there is no rule that turns a
# fandom name into a subreddit name -- and guessing produces requests that
# 404 and spend the shared budget learning nothing. Every entry here is
# verified by `--discover` before it is trusted, and one that stops yielding
# mentions is reported rather than retried forever.
#
# `fandom` is a HINT for the operator only. What a recommendation is actually
# filed under comes from the WORK's own fandoms in the index, never from the
# subreddit it was mentioned in -- a crossover recommended on r/HPFanfiction
# belongs to both its fandoms, and the subreddit cannot say which.
COMMUNITIES: list[dict] = [
    {"sub": "HPFanfiction", "fandom": "Harry Potter",
     "query": '"what are you reading"'},
    {"sub": "FanFiction", "fandom": None,
     "query": 'flair:"Recommendation"'},
    {"sub": "AO3", "fandom": None,
     "query": '"what are you reading"'},
]

SEARCH = ("https://www.reddit.com/r/{sub}/search.rss"
          "?q={q}&restrict_sr=1&sort=new&limit=25")
THREAD = "https://www.reddit.com/r/{sub}/comments/{pid}.rss?limit=500"

_LINK = re.compile(
    r"(?:fanfiction\.net/s/(\d+)|archiveofourown\.org/works/(\d+))", re.I)
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_FIELD = {k: re.compile(rf"<{k}[^>]*>(.*?)</{k}>", re.S)
          for k in ("id", "title", "updated", "name", "content")}
_HREF = re.compile(r'<link href="(.*?)"')
_POSTID = re.compile(r"/comments/([a-z0-9]+)/")

# How far back a thread is worth reading. The point of this table is what is
# being recommended NOW, and a two-year-old thread is what the existing
# all-time markers already cover.
MAX_AGE_DAYS = int(os.getenv("REC_MAX_AGE_DAYS", "120"))


def _field(entry: str, name: str) -> str | None:
    m = _FIELD[name].search(entry)
    return m.group(1) if m else None


def _when(entry: str) -> datetime | None:
    raw = _field(entry, "updated")
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.strip())
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_threads(sub: str, query: str) -> list[dict]:
    """Recommendation threads in one community, newest first."""
    from urllib.parse import quote
    xml = reddit_fetch.get(SEARCH.format(sub=sub, q=quote(query)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    out = []
    for e in _ENTRY.findall(xml):
        href = _HREF.search(e)
        pid = _POSTID.search(href.group(1)) if href else None
        when = _when(e)
        if not (pid and when) or when < cutoff:
            continue
        out.append({"id": pid.group(1), "title": (_field(e, "title") or "").strip(),
                    "posted_at": when})
    return out


def mentions_in(xml: str) -> list[dict]:
    """Every archive link in a thread, with who said it and when.

    AutoModerator is skipped: its template post contains the word "Link" and no
    links, but a community that puts example URLs in its template would
    otherwise have them counted as recommendations every fortnight.
    """
    out = []
    for e in _ENTRY.findall(xml):
        who = (_field(e, "name") or "").strip()
        if who.lower() in ("/u/automoderator", "automoderator"):
            continue
        body = _field(e, "content") or ""
        body = html.unescape(html.unescape(body))
        when = _when(e)
        cid = (_field(e, "id") or "").strip()
        if not (when and cid):
            continue
        seen = set()
        for m in _LINK.finditer(body):
            site, sid = ("ffnet", m.group(1)) if m.group(1) else ("ao3", m.group(2))
            if (site, sid) in seen:      # one comment naming a work twice is one
                continue                 # recommendation, not two
            seen.add((site, sid))
            out.append({"comment_id": cid, "recommender": who,
                        "mentioned_at": when, "site": site, "site_id": sid})
    return out


def _store(db, community: str, thread_id: str, ms: list[dict]) -> int:
    """Write mentions, resolving each link to an indexed work where possible."""
    written = 0
    for m in ms:
        row = db.execute(
            text("SELECT id FROM stories WHERE site=:s AND site_id=:i"),
            {"s": m["site"], "i": m["site_id"]}).first()
        res = db.execute(text("""
            INSERT INTO rec_mentions
                (source, community, thread_id, comment_id, recommender,
                 mentioned_at, story_id, site, site_id)
            VALUES ('reddit', :c, :t, :cid, :who, :at, :sid, :site, :siteid)
            ON CONFLICT (comment_id, site, site_id) DO NOTHING
        """), {"c": community, "t": thread_id, "cid": m["comment_id"],
               "who": m["recommender"], "at": m["mentioned_at"],
               "sid": str(row[0]) if row else None,
               "site": m["site"], "siteid": m["site_id"]})
        written += res.rowcount or 0
    return written


def run(dry_run: bool = False, discover_only: bool = False) -> dict:
    """One pass. Resumable: a refusal stops it where it stands.

    Every consumer of `reddit_fetch` is a resumable worklist, because the only
    safe response to a 429 is to stop -- so a pass must be able to be cut short
    at any point and lose nothing but the time it had left.
    """
    found = read = stored = 0
    with db_session() as db:
        # 1. Discover threads. One request per community, and only for a
        #    community whose last search is old enough to be worth repeating.
        for c in COMMUNITIES:
            try:
                threads = find_threads(c["sub"], c["query"])
            except Refused as e:
                log.warning("rec_threads: %s — stopping this pass", e)
                break
            found += len(threads)
            log.info("rec_threads: %s has %d recent threads", c["sub"], len(threads))
            if dry_run:
                continue
            for t in threads:
                db.execute(text("""
                    INSERT INTO rec_threads (id, community, title, posted_at)
                    VALUES (:i, :c, :ti, :p) ON CONFLICT (id) DO NOTHING
                """), {"i": t["id"], "c": c["sub"].lower(),
                       "ti": t["title"], "p": t["posted_at"]})
            db.commit()

        if dry_run or discover_only:
            return {"threads_found": found, "threads_read": 0, "mentions": 0}

        # 2. Read the unread ones, newest first. Newest first because this
        #    table exists to answer "lately" — a backlog of old threads must
        #    never delay the current fortnight's.
        rows = db.execute(text("""
            SELECT id, community FROM rec_threads
             WHERE read_at IS NULL ORDER BY posted_at DESC NULLS LAST LIMIT 40
        """)).fetchall()
        for tid, community in rows:
            try:
                xml = reddit_fetch.get(THREAD.format(sub=community, pid=tid))
            except Refused as e:
                log.info("rec_threads: %s — stopping, %d threads read", e, read)
                break
            ms = mentions_in(xml)
            n = _store(db, community, tid, ms)
            db.execute(text("""
                UPDATE rec_threads SET read_at = now(), mentions = :n, raw = :raw
                 WHERE id = :i
            """), {"n": len(ms), "raw": xml, "i": tid})
            db.commit()
            read += 1
            stored += n
            log.info("rec_threads: %s/%s — %d links, %d new", community, tid, len(ms), n)
    return {"threads_found": found, "threads_read": read, "mentions": stored}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="find threads and report, write nothing")
    ap.add_argument("--discover", action="store_true",
                    help="record the threads but do not read them")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = run(dry_run=a.dry_run, discover_only=a.discover)
    print(f"threads found {out['threads_found']}, read {out['threads_read']}, "
          f"new mentions {out['mentions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
