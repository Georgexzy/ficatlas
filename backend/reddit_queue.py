"""The fic-finder posts nobody has answered yet, with the search already run.

    docker exec ficatlas-backend-1 python reddit_queue.py --dry-run
    docker exec ficatlas-backend-1 python reddit_queue.py

Why this exists
---------------
The outreach panel turns a fic-finder post into a search in seconds. Finding
the posts is still a person remembering to look, and the measurement says that
is the binding constraint: **Reddit sent 7 sessions in 30 days**, against 80
from Google, on a site whose own outreach note calls those threads "the one
audience that is already asking the question FicAtlas answers".

It is also the only channel that produces a reader and an inbound LINK at the
same time, which is what the other half of the growth problem needs — Googlebot
crawls this site once a day now, because nothing anywhere links to it.

So this is a worklist: fetch the posts, run the extractor over each, and rank
by whether we actually have an answer. What it never does is post anything. The
reply is still text on a clipboard and a person still decides.

Why RSS
-------
Reddit's JSON API needs a registered app and OAuth. The flair-filtered SEARCH
feed does not:

    /r/FanFiction/search.rss?q=flair:"Lost Fic"&restrict_sr=1&sort=new

Measured: the first request returns 200 with real Lost Fic posts, and the very
next one from the same address returns 429. So the constraint here is not
access, it is PACE — one subreddit per run, a long gap between runs, and a
retry that gives up rather than hammering. If this ever needs to go faster, a
registered app raises the ceiling to 100 requests a minute; nothing else about
this file would change.

Ranking
-------
By how many works the extracted query actually finds, because that is the
question — a post we cannot answer is not worth a person's attention, and one
we can answer with four works is worth more than one that returns five
thousand. `_PROBE_CAP` counts are a floor, which is all the ordering needs.

Posts whose answer would breach the subreddit's own rules are stored with
`link_unsafe` set and never offered. See `_link_is_unsafe` in api/search.py:
the reply this feeds would carry a link, and a link that surfaces what those
rules forbid is the one outcome that must not happen automatically.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("reddit_queue")

# Where the people asking are. Flair-filtered where the subreddit uses one, so
# the feed is requests rather than every post.
# RECS WANTED FIRST, and the distinction is the whole reason this list is
# ordered rather than a set.
#
# A "Recs Wanted" post is a FILTER: "shipping fics from the POV of an angsty
# character, any fandom except the Netflix Witcher", "Ben 10 x MCU with a blank
# Omnitrix, no harem". Every one of those is a set of constraints over metadata,
# which is the question this index answers.
#
# A "Lost Fic" post is an IDENTIFICATION: one specific half-remembered work,
# recognised by a detail its metadata does not carry — a scene, a line, a cover.
# Answering it needs somebody who has read it. We can sometimes narrow the
# search, and the measurement below says how often.
#
# Both are fetched, because a narrowed Lost Fic search is still a useful reply
# and costs nothing extra. The ordering matters because a run walks these one
# at a time with a gap between them and Reddit rate-limits the unauthenticated
# feed hard — whatever is first is what reliably arrives.
FEEDS = [
    ("FanFiction",    'flair:"Recs Wanted"'),
    ("FanFiction",    'flair:"Lost Fic"'),
    ("AO3",           'flair:"Fic/Work Search"'),
    # r/HPfanfiction is deliberately absent. Its requests are a good fit and
    # this account is banned there, so fetching them would build a worklist of
    # posts nobody here can reply to.
    ("fanfiction",    None),
]

UA = os.getenv(
    "REDDIT_USER_AGENT",
    "ficatlas/1.0 (fic-finder outreach; +https://ficatlas.com)")
# Between feeds, in seconds. Reddit 429s the second unauthenticated request
# from an address arriving immediately after the first; this is the whole
# reason the job is slow on purpose.
GAP_SECONDS = float(os.getenv("REDDIT_GAP_SECONDS", "20"))
TIMEOUT = float(os.getenv("REDDIT_TIMEOUT", "20"))
# How long a post stays in the queue before it is stale. A fic-finder post gets
# its answers in a day or two; answering a three-week-old one helps nobody and
# reads as a bot.
KEEP_DAYS = int(os.getenv("REDDIT_KEEP_DAYS", "21"))

DDL = """
CREATE TABLE IF NOT EXISTS reddit_posts (
    id          text PRIMARY KEY,
    subreddit   text NOT NULL,
    title       text NOT NULL,
    body        text,
    url         text NOT NULL,
    posted_at   timestamp,
    seen_at     timestamp DEFAULT now(),
    -- What the extractor made of it, and how many works that finds. NULL until
    -- the post has been read; `works` is a probe count and therefore a floor.
    query       text,
    works       integer,
    -- Whether the reply this would produce carries a link that breaches the
    -- subreddit's own rules. Stored so it can be shown as a REASON rather than
    -- as a silent absence.
    link_unsafe boolean NOT NULL DEFAULT false,
    -- new | answered | skipped. Set by a person, in the panel.
    state       text NOT NULL DEFAULT 'new'
)
"""

_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _field(entry: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S)
    return m.group(1).strip() if m else ""


# What Reddit's feed adds to every entry, which is not what the reader wrote.
# A post that is a title and nothing else — "Any good long jjk fics?" — has a
# BODY consisting entirely of this, and the extractor duly read `[comments]`
# and returned `tag:"Comment Fic"`. Stripped before anything looks at the text.
_RSS_CHROME = re.compile(
    r"\s*submitted by\s*/?u/\S+|\[link\]|\[comments\]|"
    r"^\s*/?u/\S+\s*$", re.I | re.M)


def _text(raw: str) -> str:
    """Reddit double-escapes the post body inside <content type="html">."""
    plain = _TAG.sub(" ", html.unescape(html.unescape(raw)))
    return re.sub(r"\s+", " ", _RSS_CHROME.sub(" ", plain)).strip()


def fetch(subreddit: str, flair: str | None) -> list[dict]:
    """One feed, or nothing. Never raises: a feed that is down or rate-limited
    must not abandon the rest of the run."""
    # SORTED BY NEW, and it is a real choice rather than the default.
    #
    # `hot` surfaces the posts with the most engagement, which sounds better
    # for a reply that wants to be seen — and is the wrong end of the problem.
    # A fic-finder thread collects its answers in a day or two, so by the time
    # a post is hot it usually has them, and a late reply is one more comment
    # nobody scrolls to. `new` is where a useful answer is still the FIRST
    # useful answer, which is the only kind that gets read.
    #
    # Neither sort can tell us the thing we actually want — whether a post has
    # been answered yet — because the feed carries no comment count. `new` is
    # the closest available proxy and the queue's own state (waiting /
    # answered / skipped) carries the rest.
    sort = os.getenv("REDDIT_SORT", "new")
    if flair:
        q = urllib.parse.quote(flair, safe="")
        url = (f"https://www.reddit.com/r/{subreddit}/search.rss"
               f"?q={q}&restrict_sr=1&sort={sort}&limit=25")
    else:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.rss?limit=25"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 429 is the expected outcome, not a fault: see the module note. It
        # logs at info so a run that got nothing is visible without being
        # alarming.
        log.info("reddit: r/%s %s -> HTTP %s", subreddit, flair or "new", e.code)
        return []
    except Exception as e:
        log.info("reddit: r/%s unreachable: %s", subreddit, type(e).__name__)
        return []

    out = []
    for entry in _ENTRY.findall(body):
        link = re.search(r'<link[^>]*href="([^"]+)"', entry)
        rid = _field(entry, "id") or (link.group(1) if link else "")
        if not rid:
            continue
        out.append({
            "id": rid[:200],
            "subreddit": subreddit,
            "title": _text(_field(entry, "title"))[:400],
            "body": _text(_field(entry, "content"))[:8000],
            "url": (link.group(1) if link else "")[:500],
            "posted_at": _field(entry, "published")[:19] or None,
        })
    return out


def _read(db, post: dict) -> dict:
    """Run the extractor over one post, exactly as the panel would.

    Title AND body, because a fic-finder post routinely puts the fandom in the
    title and nothing else — "Black brothers/jegulus fic recs" is the whole
    request, and the body only qualifies it.
    """
    from api.search import extract
    text_in = f"{post['title']}\n\n{post['body']}"[:4000]
    try:
        ex = extract(text=text_in, db=db)
    except Exception:
        log.debug("extract failed for %s", post["id"], exc_info=True)
        return {"query": None, "works": None, "link_unsafe": False}
    works = None
    if ex.query:
        try:
            from api.search import _SUGGEST_CAP, _probe_count, _Term
            terms = [_Term(t.kind, t.value) for t in ex.terms
                     if t.value in ex.query]
            works = _probe_count(db, terms, ex.word_count_min, ex.status,
                                 ex.crossovers, cap=_SUGGEST_CAP,
                                 word_count_max=ex.word_count_max)
        except Exception:
            log.debug("probe failed for %s", post["id"], exc_info=True)
    return {"query": ex.query or None, "works": works,
            "link_unsafe": bool(ex.link_unsafe)}


def run(dry_run: bool = False, limit_feeds: int | None = None) -> dict:
    feeds = FEEDS[:limit_feeds] if limit_feeds else FEEDS
    posts: list[dict] = []
    for i, (sub, flair) in enumerate(feeds):
        if i:
            time.sleep(GAP_SECONDS)
        got = fetch(sub, flair)
        log.info("reddit: r/%s %s -> %d posts", sub, flair or "new", len(got))
        posts.extend(got)

    stats = {"fetched": len(posts), "new": 0, "answerable": 0}
    if not posts:
        return stats

    with db_session() as db:
        if not dry_run:
            db.execute(text(DDL))
            db.commit()
        known = set()
        if not dry_run:
            known = {r[0] for r in db.execute(text(
                "SELECT id FROM reddit_posts WHERE id = ANY(:ids)"),
                {"ids": [p["id"] for p in posts]}).fetchall()}

        for p in posts:
            if p["id"] in known:
                continue
            stats["new"] += 1
            read = _read(db, p)
            if read["works"] and not read["link_unsafe"]:
                stats["answerable"] += 1
            if dry_run:
                log.info("  %-14s %-58s works=%s%s", "r/" + p["subreddit"],
                         p["title"][:58], read["works"],
                         "  UNSAFE" if read["link_unsafe"] else "")
                continue
            db.execute(text("""
                INSERT INTO reddit_posts
                    (id, subreddit, title, body, url, posted_at,
                     query, works, link_unsafe)
                VALUES (:id, :sub, :title, :body, :url,
                        CAST(NULLIF(:posted, '') AS timestamp),
                        :q, :w, :unsafe)
                ON CONFLICT (id) DO NOTHING
            """), {"id": p["id"], "sub": p["subreddit"], "title": p["title"],
                   "body": p["body"], "url": p["url"],
                   "posted": (p["posted_at"] or "").replace("T", " "),
                   "q": read["query"], "w": read["works"],
                   "unsafe": read["link_unsafe"]})
        if not dry_run:
            # Stale posts drop out rather than accumulating. A queue that only
            # grows is one nobody opens.
            db.execute(text(
                "DELETE FROM reddit_posts WHERE seen_at < now() - make_interval(days => :d)"),
                {"d": KEEP_DAYS})
            db.commit()

    log.info("reddit_queue: %d fetched, %d new, %d answerable",
             stats["fetched"], stats["new"], stats["answerable"])
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--feeds", type=int, default=None,
                    help="only the first N feeds, for a trial run")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run, limit_feeds=args.feeds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
