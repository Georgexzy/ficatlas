"""Ground truth for the extractor: what a lost-fic post turned out to be.

The problem
-----------
There was no way to tell whether a change to the extractor helped. It produces
a query; whether that query would have found the fic the reader was actually
describing is exactly the thing nobody could measure, so every improvement was
argued from a handful of examples read by eye.

Measuring it needs pairs of (the post, the fic it turned out to be), and those
live in the comments of lost-fic threads -- which looked unreachable. The JSON
API returns 403 without OAuth, and old.reddit.com serves an interstitial.

The way in
----------
Per-post comment feeds are public Atom, exactly like the flair-filtered search
feeds this project already uses:

    /r/FanFiction/comments/<post_id>.rss

Measured: HTTP 200, the post and its replies as <entry> elements, no
credentials. The catch is the one reddit_queue already documents in its own
module note -- "the constraint here is not access, it is PACE". Requests
arriving back to back get 429, so this runs on the same long gap and gives up
rather than retrying hard.

What counts as an answer
------------------------
Not a flair. r/FanFiction has no "solved" flair, and waiting for one would
throw away most of the corpus. A Lost Fic post whose comments contain a link to
a work THIS INDEX HOLDS is a usable pair on its own: somebody asked for a fic
in prose, somebody else named it, and we can look up what that fic is tagged
with.

It is a weak label and is treated as one. A commenter can link the wrong fic,
or a similar one, or their own. So `confirmed` records the stronger signal --
the original poster replying under that link, which is how Reddit says "yes,
that was it" -- and the evaluation can be run against either.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

UA = os.getenv("REDDIT_USER_AGENT",
               "ficatlas/1.0 (fic-finder outreach; +https://ficatlas.com)")
# The same gap reddit_queue uses, for the same measured reason: the second
# unauthenticated request from an address arriving straight after the first
# comes back 429.
GAP_SECONDS = float(os.getenv("REDDIT_GAP_SECONDS", "20"))
TIMEOUT = float(os.getenv("REDDIT_TIMEOUT", "20"))

DDL = """
CREATE TABLE IF NOT EXISTS reddit_answers (
    post_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    story_id    UUID,
    author      TEXT,
    -- The original poster replied under this link. Reddit's way of saying
    -- "yes, that was the one", and the difference between a suggestion and an
    -- answer.
    confirmed   BOOLEAN NOT NULL DEFAULT FALSE,
    found_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, url)
);
CREATE INDEX IF NOT EXISTS ix_reddit_answers_story ON reddit_answers (story_id)
    WHERE story_id IS NOT NULL;
-- Which posts have already been asked about, so a pass does not re-fetch them.
ALTER TABLE reddit_posts ADD COLUMN IF NOT EXISTS answers_checked_at TIMESTAMPTZ;
"""

# Work links, in the two shapes the archives actually publish. Chapter and
# query suffixes are deliberately tolerated and then discarded -- readers paste
# whatever was in the address bar.
_AO3 = re.compile(r"archiveofourown\.org/works/(\d+)", re.I)
_FFN = re.compile(r"fanfiction\.net/s/(\d+)", re.I)


def fic_links(text: str) -> list[tuple[str, str]]:
    """(site, site_id) for every work link in a comment.

    Deduplicated, order preserved: a commenter who links the same fic twice --
    once as a title, once as a bare URL -- is naming one fic, not two.
    """
    out: list[tuple[str, str]] = []
    seen = set()
    for site, pat in (("ao3", _AO3), ("ffnet", _FFN)):
        for m in pat.finditer(text or ""):
            key = (site, m.group(1))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _entries(xml: str) -> list[dict]:
    """Author and body for each <entry> in a comment feed."""
    out = []
    for raw in re.findall(r"<entry>(.*?)</entry>", xml or "", re.S):
        author = re.search(r"<name>\s*(.*?)\s*</name>", raw, re.S)
        content = re.search(r'<content type="html">(.*?)</content>', raw, re.S)
        body = content.group(1) if content else ""
        # Reddit double-escapes the HTML inside the Atom content element, so
        # one unescape leaves &amp;lt;a href=... and no link matches.
        body = html.unescape(html.unescape(body))
        out.append({"author": (author.group(1) if author else "").strip(),
                    "body": body})
    return out


# Refused means Reddit declined to answer, and says NOTHING about the post.
#
# Distinct from an empty reply list, and the distinction is the whole point:
# conflating them marks a post as checked because we were rate-limited, and it
# is then never asked about again. The FF.net enrichment lost queued captures to
# exactly this shape -- a 429 returned the same value as "there is nothing
# there" -- and it cost a day of throughput before anyone could see it, because
# the logs said the archive was empty.
#
# It now comes from reddit_fetch, which owns the one budget every Reddit
# consumer here shares, rather than being defined once per job.
import reddit_fetch                      # noqa: E402
from reddit_fetch import Refused          # noqa: E402


def fetch_comments(subreddit: str, post_id: str) -> list[dict]:
    """The post and its replies. Raises Refused when Reddit will not answer.

    THROUGH THE SHARED BUDGET, and that is not a tidying-up. This paced itself
    with its own sleep against a limit that is per-ADDRESS, as did the
    fic-finder queue, as would the recommendation harvest — three jobs each
    behaving impeccably on its own and together asking far too often. Observed
    in the worker log before this changed: a 429 here every ~100 seconds,
    indefinitely, which is not merely this job failing, it is this job spending
    the allowance the other two need. See reddit_fetch.py.
    """
    pid = post_id.split("_", 1)[-1]        # t3_abc123 -> abc123
    url = f"https://www.reddit.com/r/{subreddit}/comments/{pid}.rss"
    return _entries(reddit_fetch.get(url))


def answers_in(entries: list[dict], op: str | None) -> list[dict]:
    """Work links from the replies, with the ones the OP endorsed marked.

    `confirmed` is set when the ORIGINAL POSTER also links that work, which is
    what "yes, that was it" looks like once a thread is flattened into a feed:
    the OP either replies with the link or repeats it in an edit. Anything else
    is a suggestion, and suggestions are wrong often enough that the evaluation
    has to be able to tell the two apart.
    """
    by_url: dict[tuple[str, str], dict] = {}
    op_links: set[tuple[str, str]] = set()
    for e in entries:
        links = fic_links(e["body"])
        is_op = bool(op) and e["author"].lstrip("/u/") == op.lstrip("/u/")
        for key in links:
            if is_op:
                op_links.add(key)
            by_url.setdefault(key, {"site": key[0], "site_id": key[1],
                                    "author": e["author"]})
    for key in op_links:
        if key in by_url:
            by_url[key]["confirmed"] = True
    return [{**v, "confirmed": v.get("confirmed", False)} for v in by_url.values()]


def _resolve(db, site: str, site_id: str):
    """Our story id for a linked work, or None if we do not hold it."""
    from sqlalchemy import text as sql_text
    return db.execute(sql_text(
        "SELECT id FROM stories WHERE site = :s AND site_id = :i LIMIT 1"),
        {"s": site, "i": site_id}).scalar()


def harvest(db, limit: int = 20, sleep=time.sleep) -> dict:
    """Ask a few unchecked posts what they turned out to be.

    OLDEST first, which is the opposite of what the outreach queue wants and
    right for the opposite reason. Outreach wants posts nobody has answered
    yet, so it works newest-first; this wants posts somebody HAS answered, and
    a thread three hours old has had no time to be. reddit_posts keeps three
    weeks, so oldest-unchecked is the most-likely-answered end of it.

    A post is marked checked whether or not it yielded anything, because
    "nobody answered this one" is a fact worth keeping and re-asking costs a
    request we are strictly rationed on. Being REFUSED is not that fact, and
    leaves the post unchecked -- see Refused.
    """
    from sqlalchemy import text as sql_text

    rows = db.execute(sql_text("""
        SELECT id, subreddit, url FROM reddit_posts
         WHERE answers_checked_at IS NULL
         ORDER BY posted_at ASC NULLS LAST
         LIMIT :lim
    """), {"lim": limit}).fetchall()

    stats = {"posts": 0, "links": 0, "resolved": 0, "confirmed": 0,
             "refused": 0}
    for post_id, subreddit, url in rows:
        stats["posts"] += 1
        # The author is in the post URL: /r/sub/comments/<id>/<slug>/ has no
        # author, so it is read from the feed's first entry instead — that
        # entry IS the post.
        try:
            entries = fetch_comments(subreddit, post_id)
        except Refused as e:
            # Leave it unchecked. Being throttled is not a fact about this
            # post, and marking it would retire it for good.
            stats["refused"] += 1
            log.info("reddit answers: %s, stopping this pass", e)
            break
        op = entries[0]["author"] if entries else None

        # KEEP THE THREAD, not just the links we could parse out of it.
        #
        # Reddit is the rate-limited half of this job — a pass reads ONE post
        # and then takes an HTTP 429 — so the fetch is the expensive resource
        # and it was being thrown away the moment `fic_links` had run over it.
        # Anything that regex misses is lost until the post is fetched again,
        # which in practice is never.
        #
        # And it misses the commonest shape of answer there is. `fic_links`
        # finds URLs; a great many replies are "it's Manacled by SenLinYu" with
        # no link at all, because the person answering is typing from memory on
        # a phone. Those are exactly the answers worth having: a title and an
        # author resolve against this index perfectly well.
        #
        # Stored so a later pass can read them again without costing another
        # request — by a better parser, or by a model, whichever turns out to
        # find more. Capped at 40k because a long thread is mostly people
        # thanking each other, and the answers are near the top.
        try:
            db.execute(sql_text("""
                UPDATE reddit_posts SET comments = :c WHERE id = :p
            """), {"p": post_id,
                   "c": "\n\n---\n\n".join(
                       f"{e.get('author', '?')}: {e.get('body', '')}"
                       for e in entries)[:40000]})
        except Exception:
            # Never fail a harvest pass over the archive copy — the links are
            # the job, this is the bonus.
            log.debug("could not store comments for %s", post_id, exc_info=True)

        for a in answers_in(entries, op):
            stats["links"] += 1
            sid = _resolve(db, a["site"], a["site_id"])
            if sid:
                stats["resolved"] += 1
            if a["confirmed"]:
                stats["confirmed"] += 1
            link = (f"https://archiveofourown.org/works/{a['site_id']}"
                    if a["site"] == "ao3"
                    else f"https://www.fanfiction.net/s/{a['site_id']}/1/")
            db.execute(sql_text("""
                INSERT INTO reddit_answers (post_id, url, story_id, author, confirmed)
                VALUES (:p, :u, :s, :a, :c)
                ON CONFLICT (post_id, url) DO UPDATE
                   SET story_id  = COALESCE(EXCLUDED.story_id, reddit_answers.story_id),
                       confirmed = reddit_answers.confirmed OR EXCLUDED.confirmed
            """), {"p": post_id, "u": link, "s": sid,
                   "a": a["author"][:80], "c": a["confirmed"]})
        db.execute(sql_text(
            "UPDATE reddit_posts SET answers_checked_at = now() WHERE id = :p"),
            {"p": post_id})
        db.commit()
        # No sleep here any more. reddit_fetch owns the pacing for every Reddit
        # consumer in this project, and a second sleep on top of it is not
        # belt-and-braces — it is a second opinion about a budget that only
        # works if there is one. `sleep` stays in the signature because the
        # tests inject it, and because a caller may still want to be gentler
        # than the floor.
        sleep(0)
    return stats
