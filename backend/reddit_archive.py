"""Reddit, read from the public Reddit archives instead of from Reddit.

Why
---
Reddit answers this server's unauthenticated requests about one time in six
(measured 2026-10-04: 429, 200, 429, 429, 429, 429 at 10-second spacing, the
worker container 429 on every feed it uses), and registering an API app is not
available to the operator. Every Reddit consumer here had been backed off at
the 6-hour ceiling since 2026-09-29, so "what a fandom is recommending lately"
and the fic-finder queue had produced nothing for a week.

Two public archives serve the same posts and comments over open JSON APIs:

  Arctic Shift  arctic-shift.photon-reddit.com   near-real-time (posts appear
                within minutes), filters on flair, serves a thread's comments.
  PullPush      api.pullpush.io                  Pushshift-compatible; supports
                free-text search, which Arctic Shift refused (422). Lags by
                months, which is fine for its one use: the rec-threads phrase
                searches look back 120 days.

How
---
The three consumers (reddit_queue, reddit_answers, rec_threads) parse Reddit's
Atom feeds and are tested against that shape. Rather than rewrite all three,
`atom_for(url)` takes the Reddit feed URL they would have fetched, answers it
from an archive, and renders the result in Reddit's own Atom layout —
double-escaped HTML in <content type="html">, "/u/name" authors, and a
permalink carrying /comments/<id>/. reddit_fetch.get() asks here first and only
goes to Reddit if this returns None or fails.

Only links and metadata are used downstream, as before: the archives are read
for which works people recommend, never to republish what they wrote.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("reddit_archive")

ARCTIC = "https://arctic-shift.photon-reddit.com/api"
PULLPUSH = "https://api.pullpush.io/reddit/search"
UA = os.getenv("REDDIT_ARCHIVE_UA",
               "ficatlas/1.0 (fanfiction index; +https://github.com/Georgexzy/ficatlas)")
TIMEOUT = float(os.getenv("REDDIT_ARCHIVE_TIMEOUT", "30"))
# Both archives are volunteer services. One request a second from this whole
# process is far below anything either documents, and these jobs make a few
# hundred requests a day at most.
MIN_GAP = float(os.getenv("REDDIT_ARCHIVE_MIN_GAP", "1.0"))
ENABLED = os.getenv("REDDIT_SOURCE", "archive").lower() != "reddit"

_lock = threading.Lock()
_last = 0.0

_SEARCH = re.compile(r"^/r/([^/]+)/search\.rss$")
_LISTING = re.compile(r"^/r/([^/]+)/(new|hot|top|rising)\.rss$")
_THREAD = re.compile(r"^/r/([^/]+)/comments/([a-z0-9]+)(?:/[^/]*)?/?\.rss$")
_FLAIR = re.compile(r'^\s*flair:"([^"]+)"\s*$')


# A heavy query is answered "Timeout. Maybe slow down a bit" as a 422, and a
# busy moment as 429/5xx. Those pass, so they are retried here with a short
# backoff. Raising instead sent the caller to its fallback -- Reddit itself,
# which waits ~8 minutes for its own rate budget and then refuses this server
# anyway: measured, a 5,000-thread read managed 30 threads in 90 minutes.
_RETRY_AFTER = (5, 20, 60)
_TRANSIENT = {422, 429, 500, 502, 503, 504}


def _get_json(url: str) -> dict:
    global _last
    for attempt, backoff in enumerate((*_RETRY_AFTER, None)):
        with _lock:
            wait = MIN_GAP - (time.monotonic() - _last)
            if wait > 0:
                time.sleep(wait)
            _last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in _TRANSIENT or backoff is None:
                raise
        except (urllib.error.URLError, TimeoutError):
            if backoff is None:
                raise
        time.sleep(backoff)
    raise RuntimeError("unreachable")


def _iso(ts) -> str:
    return datetime.fromtimestamp(float(ts or 0), tz=timezone.utc).isoformat()


def _esc(text: str) -> str:
    # Reddit double-escapes the HTML inside <content>; every consumer unescapes
    # twice, so this must escape twice to round-trip exactly.
    return html.escape(html.escape(text or "", quote=False), quote=False)


def _entry(*, kind: str, rid: str, sub: str, author: str, created, title: str,
           body: str, permalink: str) -> str:
    link = "https://www.reddit.com" + (permalink or f"/r/{sub}/comments/{rid}/")
    return (
        "<entry>"
        f"<author><name>/u/{html.escape(author or '[deleted]')}</name></author>"
        f'<content type="html">{_esc(body)}</content>'
        f"<id>{kind}_{rid}</id>"
        f'<link href="{html.escape(link)}" />'
        f"<updated>{_iso(created)}</updated>"
        f"<published>{_iso(created)}</published>"
        f"<title>{html.escape(title or '')}</title>"
        "</entry>"
    )


def _post_entry(p: dict) -> str:
    sub = p.get("subreddit") or ""
    return _entry(kind="t3", rid=p["id"], sub=sub, author=p.get("author"),
                  created=p.get("created_utc"), title=p.get("title"),
                  body=p.get("selftext") or "", permalink=p.get("permalink"))


def _comment_entry(c: dict, sub: str, pid: str) -> str:
    return _entry(kind="t1", rid=c["id"], sub=sub, author=c.get("author"),
                  created=c.get("created_utc"), title="",
                  body=c.get("body") or "",
                  permalink=c.get("permalink") or f"/r/{sub}/comments/{pid}/_/{c['id']}/")


def _feed(entries: list[str]) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>")


def _posts(sub: str, *, flair: str | None, text: str | None, limit: int) -> list[dict]:
    if text:
        # Free text: PullPush (Arctic Shift answers 422 to these).
        q = urllib.parse.urlencode({"subreddit": sub, "q": text, "size": min(limit, 100),
                                    "sort": "desc", "sort_type": "created_utc"})
        return _get_json(f"{PULLPUSH}/submission/?{q}").get("data") or []
    params = {"subreddit": sub, "limit": min(limit, 100), "sort": "desc"}
    if flair:
        params["link_flair_text"] = flair
    return _get_json(f"{ARCTIC}/posts/search?{urllib.parse.urlencode(params)}").get("data") or []


def _thread(sub: str, pid: str, limit: int) -> list[str]:
    out: list[str] = []
    post = (_get_json(f"{ARCTIC}/posts/ids?ids={pid}").get("data") or [])
    if post:
        out.append(_post_entry(post[0]))
    # Comments oldest first, paged by creation time: a busy recommendation
    # thread runs past one page of 100.
    after = None
    got = 0
    while got < limit:
        params = {"link_id": pid, "limit": 100, "sort": "asc"}
        if after:
            params["after"] = after
        page = _get_json(f"{ARCTIC}/comments/search?{urllib.parse.urlencode(params)}").get("data") or []
        if not page:
            break
        for c in page:
            out.append(_comment_entry(c, sub, pid))
        got += len(page)
        if len(page) < 100:
            break
        after = int(page[-1]["created_utc"])
    return out


def atom_for(url: str) -> str | None:
    """The Atom feed Reddit would have served for `url`, from an archive.

    None when the URL is not a shape this knows, so the caller can fall back to
    Reddit. Raises on a network or API failure, which the caller also treats as
    "try Reddit".
    """
    if not ENABLED:
        return None
    u = urllib.parse.urlsplit(url)
    if not u.netloc.endswith("reddit.com"):
        return None
    qs = dict(urllib.parse.parse_qsl(u.query))
    limit = int(qs.get("limit") or 25)

    m = _THREAD.match(u.path)
    if m:
        return _feed(_thread(m.group(1), m.group(2), max(limit, 100)))
    m = _SEARCH.match(u.path)
    if m:
        q = qs.get("q", "")
        f = _FLAIR.match(q)
        posts = _posts(m.group(1), flair=f.group(1) if f else None,
                       text=None if f else q.strip('"'), limit=limit)
        return _feed([_post_entry(p) for p in posts if p.get("id")])
    m = _LISTING.match(u.path)
    if m:
        posts = _posts(m.group(1), flair=None, text=None, limit=limit)
        return _feed([_post_entry(p) for p in posts if p.get("id")])
    return None
