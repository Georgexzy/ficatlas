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
import time
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
#
# `kind` says what the replies ARE:
#   thread   a recurring "what are you reading" post: every link is a rec.
#   request  someone asks for fics and the replies answer with links. The
#            REQUESTER's own links are not recommendations -- they are what
#            they have already read, or the fic they want more like -- so a
#            request thread counts replies by other people only.
#   recs     a post whose author is recommending: every link counts.
#
# Flair queries, not free text, wherever a community flairs its posts.
# reddit_archive answers flair from Arctic Shift, which is minutes fresh;
# free text can only go to PullPush, which lags by MONTHS -- so a text query
# here finds last spring's threads and none of this fortnight's. Every flair
# below was read off the community's own recent posts (2026-10-05), and the
# ones that are about IDENTIFYING a fic ("Find That Fic", "Lost Fic") are left
# out on purpose: an answer there is "this is the one you mean", not "read it".
COMMUNITIES: list[dict] = [
    {"sub": "HPFanfiction", "fandom": "Harry Potter", "kind": "thread",
     "query": '"what are you reading"'},
    {"sub": "HPFanfiction", "fandom": "Harry Potter", "kind": "request",
     "query": 'flair:"Request"'},
    {"sub": "HPFanfiction", "fandom": "Harry Potter", "kind": "recs",
     "query": 'flair:"Recommendation"'},
    {"sub": "FanFiction", "fandom": None, "kind": "request",
     "query": 'flair:"Recs Wanted"'},
    {"sub": "AO3", "fandom": None, "kind": "thread",
     "query": '"what are you reading"'},
    {"sub": "TheCitadel", "fandom": "A Song of Ice and Fire", "kind": "request",
     "query": 'flair:"Wanted: Fanfic Recommendations - NOT DISCUSSION"'},
    {"sub": "NarutoFanfiction", "fandom": "Naruto", "kind": "request",
     "query": 'flair:"Fic Request"'},
    {"sub": "MarvelFanfiction", "fandom": "Marvel", "kind": "request",
     "query": 'flair:"Looking For Recs"'},
    {"sub": "PercyJacksonFanfic", "fandom": "Percy Jackson", "kind": "recs",
     "query": 'flair:"Recommendation🥇"'},
    {"sub": "Dramione", "fandom": "Harry Potter", "kind": "request",
     "query": 'flair:":recs: Recs Wanted!"'},
    {"sub": "Drarry", "fandom": "Harry Potter", "kind": "request",
     "query": 'flair:"Give me fic recommendations"'},
    {"sub": "Drarry", "fandom": "Harry Potter", "kind": "recs",
     "query": 'flair:"Fics I recommend"'},
    {"sub": "WormFanfic", "fandom": "Worm", "kind": "request",
     "query": 'flair:"Fic Search - General"'},
]

# Replies to a request arrive over days, and a thread is read ONCE. Reading it
# the hour it is posted records the question and almost none of the answers.
READ_AFTER_DAYS = float(os.getenv("REC_READ_AFTER_DAYS", "3"))
READ_PER_PASS = int(os.getenv("REC_READ_PER_PASS", "120"))

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


def mentions_in(xml: str, skip_op: bool = False) -> list[dict]:
    """Every archive link in a thread, with who said it and when.

    AutoModerator is skipped: its template post contains the word "Link" and no
    links, but a community that puts example URLs in its template would
    otherwise have them counted as recommendations every fortnight.

    `skip_op` drops everything the thread's own author wrote -- the post and
    their replies -- for request threads, where those links are what the
    requester has already read, not what anyone is recommending.
    """
    entries = _ENTRY.findall(xml)
    op = None
    if skip_op:
        for e in entries:
            if (_field(e, "id") or "").strip().startswith("t3_"):
                op = (_field(e, "name") or "").strip().lower()
                break
    out = []
    for e in entries:
        who = (_field(e, "name") or "").strip()
        if who.lower() in ("/u/automoderator", "automoderator"):
            continue
        if op and who.lower() == op and op not in ("/u/[deleted]", ""):
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


# One definition of "which work does this link mean", shared with the search
# box's pasted links. See work_lookup.py.
from work_lookup import resolve  # noqa: E402


def reresolve(db, limit: int = 2000) -> int:
    """Attach mentions whose work was not indexed when they were read.

    The FF.net listing harvest and the recent-works crawler add works every
    day, so "not indexed" is a state that ends; nothing used to look again.
    """
    rows = db.execute(text("""
        SELECT DISTINCT site, site_id FROM rec_mentions
         WHERE story_id IS NULL AND site IS NOT NULL LIMIT :n
    """), {"n": limit}).fetchall()
    fixed = 0
    for site, sid in rows:
        story = resolve(db, site, sid)
        if story:
            fixed += db.execute(text("""
                UPDATE rec_mentions SET story_id = :st
                 WHERE story_id IS NULL AND site = :s AND site_id = :i
            """), {"st": story, "s": site, "i": sid}).rowcount or 0
    db.commit()
    return fixed


def _store(db, community: str, thread_id: str, ms: list[dict]) -> int:
    """Write mentions, resolving each link to an indexed work where possible."""
    written = 0
    for m in ms:
        found = resolve(db, m["site"], m["site_id"])
        row = (found,) if found else None
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


def run(dry_run: bool = False, discover_only: bool = False,
        read_limit: int | None = None, discover: bool = True) -> dict:
    """One pass. Resumable: a refusal stops it where it stands.

    Every consumer of `reddit_fetch` is a resumable worklist, because the only
    safe response to a 429 is to stop -- so a pass must be able to be cut short
    at any point and lose nothing but the time it had left.
    """
    found = read = stored = 0
    with db_session() as db:
        # 1. Discover threads. One request per community, and only for a
        #    community whose last search is old enough to be worth repeating.
        for c in (COMMUNITIES if discover else []):
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
                    INSERT INTO rec_threads (id, community, title, posted_at, kind)
                    VALUES (:i, :c, :ti, :p, :k) ON CONFLICT (id) DO NOTHING
                """), {"i": t["id"], "c": c["sub"].lower(),
                       "ti": t["title"], "p": t["posted_at"], "k": c["kind"]})
            db.commit()

        if dry_run or discover_only:
            return {"threads_found": found, "threads_read": 0, "mentions": 0}

        attached = reresolve(db)
        if attached:
            log.info("rec_threads: %d earlier mentions now match an indexed work", attached)

        # 2. Read the unread ones, newest first. Newest first because this
        #    table exists to answer "lately" — a backlog of old threads must
        #    never delay the current fortnight's.
        rows = db.execute(text("""
            SELECT id, community, kind FROM rec_threads
             WHERE read_at IS NULL
               AND posted_at < now() - make_interval(secs => :wait)
             ORDER BY posted_at DESC NULLS LAST LIMIT :n
        """), {"wait": READ_AFTER_DAYS * 86400,
               "n": read_limit or READ_PER_PASS}).fetchall()
        for tid, community, kind in rows:
            try:
                xml = reddit_fetch.get(THREAD.format(sub=community, pid=tid))
            except Refused as e:
                log.info("rec_threads: %s — stopping, %d threads read", e, read)
                break
            ms = mentions_in(xml, skip_op=(kind == "request"))
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


def backfill(days: int = 90) -> dict:
    """Record every flaired thread of the last `days`, paging the archive.

    The routine search sees the newest 25 per community, which is right for
    keeping up and useless for starting: the hub block needs several people
    agreeing over 30 or 90 days, and a table that starts empty takes a season
    to get there. One-off; afterwards the regular pass keeps up. Text-query
    communities are skipped -- PullPush cannot page by date usefully, and the
    thread kind they find is fortnightly anyway.
    """
    import json as _json
    import urllib.parse
    import reddit_archive as ra

    since = datetime.now(timezone.utc) - timedelta(days=days)
    found = 0
    with db_session() as db:
        for c in COMMUNITIES:
            m = re.match(r'^flair:"(.+)"$', c["query"])
            if not m:
                continue
            before = None
            while True:
                params = {"subreddit": c["sub"], "link_flair_text": m.group(1),
                          "limit": 100, "sort": "desc",
                          "after": since.strftime("%Y-%m-%d")}
                if before:
                    params["before"] = before
                page = None
                # The archive is a volunteer service and says so when a query
                # is heavy ("Timeout. Maybe slow down a bit", as a 422): wait
                # and ask again, and give up on this community, not the run.
                for wait in (15, 60, 180, None):
                    try:
                        page = ra._get_json(f"{ra.ARCTIC}/posts/search?"
                                            + urllib.parse.urlencode(params)).get("data") or []
                        break
                    except Exception as e:
                        log.info("rec_threads backfill: %s refused (%s)%s", c["sub"], e,
                                 f", retrying in {wait}s" if wait else ", skipping")
                        if wait is None:
                            break
                        time.sleep(wait)
                if page is None:
                    break
                for p in page:
                    db.execute(text("""
                        INSERT INTO rec_threads (id, community, title, posted_at, kind)
                        VALUES (:i, :c, :t, to_timestamp(:p), :k)
                        ON CONFLICT (id) DO NOTHING
                    """), {"i": p["id"], "c": c["sub"].lower(), "t": p.get("title"),
                           "p": p["created_utc"], "k": c["kind"]})
                db.commit()
                found += len(page)
                if len(page) < 100:
                    break
                before = int(page[-1]["created_utc"])
            log.info("rec_threads backfill: %s %s — %d so far", c["sub"], m.group(1), found)
    return {"threads": found}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="find threads and report, write nothing")
    ap.add_argument("--discover", action="store_true",
                    help="record the threads but do not read them")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="record every flaired thread of the last DAYS, then read them all")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.backfill:
        print(backfill(a.backfill))
        out = run(read_limit=100000, discover=False)
        print(out)
        return 0
    out = run(dry_run=a.dry_run, discover_only=a.discover)
    print(f"threads found {out['threads_found']}, read {out['threads_read']}, "
          f"new mentions {out['mentions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
