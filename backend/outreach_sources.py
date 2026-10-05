"""Fic requests from outside Reddit, into the same outreach worklist.

    docker exec ficatlas-worker-1 python outreach_sources.py --dry-run

Why these two
-------------
Researched and measured 2026-10-05 from this server, for "where else do people
ask for fic, and can we read it":

  Bluesky   api.bsky.app searchPosts answers unauthenticated (public.api.bsky.app
            refuses search with 403). Request-shaped posts run at a few a day:
            "any fic recs" ~2/week, "looking for a fic" ~3.5/week, "fic recs"
            ~3/day of which perhaps a third are asks. Low, and the asks are
            real people with a specific want.
  Tumblr    where fic-finding has lived since LiveJournal: ask boxes on fic
            library blogs and the #fic finder / #fic search tags. The API needs
            a registered key, but the public tag page carries the newest posts
            in its server-rendered state, so one page per tag is readable.
  Mastodon  #ficrec had 40 posts in 82 days. Not worth a request.
  Lemmy     a fic-recs search returned no fandom posts at all.
  SpaceBattles / Sufficient Velocity
            readable over RSS, but their rec culture is long-running thread
            pages, not request posts: nothing to answer one at a time.
  Discord, X, Wattpad
            closed or hostile to reading; those are manual channels, listed in
            the admin panel's playbook instead.

What it never does is post. Like the Reddit queue it only collects and reads;
a person decides, and replies under their own name. A reply on Tumblr is a
reblog, on Bluesky a reply under 300 characters — the panel composes both.

Each post goes through reddit_queue._read, the same extractor and probe, so a
Bluesky ask is ranked by whether this index can answer it exactly as a Reddit
one is, and the same link-safety refusal applies.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import text

log = logging.getLogger("outreach_sources")

UA = "ficatlas/1.0 (fanfiction index; +https://ficatlas.com)"
BSKY_SEARCH = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BSKY_QUERIES = ['"any fic recs"', '"looking for a fic"', '"fic recs"',
                '"fic rec"', '"fanfic recs"', '"looking for fanfic"',
                '"recommend me a fic"', '"fic recommendations"']
TUMBLR_TAGS = ["fic finder", "fic search", "looking for a fic", "fic request",
               "fic rec request", "fic recs", "fanfic recs"]
GAP = 1.5           # seconds between requests to either service

# The ask, not the offer. "fic recs" finds both "any fic recs for X?" and
# "here are my fic recs", and only the first is somebody to help. Measured on
# the first dry run: an ask-phrase alone let through "not me looking for fanfic
# fodder lmao", "my bf recommend me a fic and i was praising the author",
# "edit: found!!" and a shout-out list. So a post needs an ask phrase AND a
# sign it is addressed to somebody (a question mark, or please/anyone/someone),
# AND none of the tells that it is reporting rather than asking.
_ASK = re.compile(
    r"\b(?:looking for (?:a |an |some |any |good )?(?:fic|fics|fanfic|fanfiction|story|stories|recs?)"
    r"|any (?:good |fun |\w+ )?(?:fic |fanfic )?(?:recs?|recommendations)"
    r"|any (?:good )?fics? (?:where|with|about|that)"
    r"|(?:can|could|would) (?:any(?:one|body)|some(?:one|body)|you) (?:rec|recommend|help me find|suggest)"
    r"|does any(?:one|body) (?:know|have) (?:a |any |of )?(?:fic|fics|recs?|recommendations|a story)"
    r"|(?:pls |please |plz )?(?:some(?:one|body) )?recommend me (?:a |some )?(?:fic|fics|fanfic)"
    r"|rec me (?:a |some )?(?:fic|fics)|help me find|need (?:some )?(?:fic )?recs"
    r"|fic (?:rec )?request|i(?:'m| am) (?:searching|looking) for)\b", re.I)
_ADDRESSED = re.compile(r"\?|\b(?:pls|please|plz|any(?:one|body)|some(?:one|body)|help)\b", re.I)
_FIC = re.compile(r"\b(?:fics?|fanfics?|fanfiction|ao3|ffn|recs?|x reader|oneshots?|story)\b", re.I)
_ARCHIVE_LINK = re.compile(r"archiveofourown\.org/works/\d+|fanfiction\.net/s/\d+", re.I)
# Reporting, promoting or already solved: not something to answer.
_NOT_AN_ASK = re.compile(
    r"\b(?:commissions? open|my (?:new )?fic|i (?:wrote|posted|updated)|new chapter"
    r"|chapter \d+ is (?:up|out)|read it here|link in bio|found it|edit:? found|found!"
    r"|shout ?out|thank(?:s| you) for (?:the|all) (?:recs?|recommendations)"
    r"|not me|praising|recommended me|(?:bf|gf|friend|partner) recommend)", re.I)


def looks_like_request(text_in: str) -> bool:
    t = text_in or ""
    if len(_ARCHIVE_LINK.findall(t)) >= 2:   # a list of recs, not an ask
        return False
    if _NOT_AN_ASK.search(t):
        return False
    return bool(_ASK.search(t) and _ADDRESSED.search(t) and _FIC.search(t))


def _get(url: str, accept: str = "application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _post(*, source, community, pid, text_in, url, posted_at, flair) -> dict:
    lines = [l.strip() for l in (text_in or "").splitlines() if l.strip()]
    title = (lines[0] if lines else "")[:200]
    return {"id": f"{source}:{pid}", "source": source, "subreddit": community,
            "title": title or "(no text)", "body": "\n".join(lines[1:])[:6000],
            "url": url[:500], "posted_at": posted_at, "flair": flair}


def fetch_bluesky(queries=BSKY_QUERIES, limit: int = 50) -> list[dict]:
    out, seen = [], set()
    for q in queries:
        try:
            d = json.loads(_get(BSKY_SEARCH + "?" + urllib.parse.urlencode(
                {"q": q, "sort": "latest", "limit": limit, "lang": "en"})))
        except Exception as e:
            log.info("bluesky %s: %s", q, e)
            time.sleep(GAP)
            continue
        for p in d.get("posts", []):
            rec = p.get("record") or {}
            if rec.get("reply"):        # a reply in someone else's thread
                continue
            uri, handle = p.get("uri", ""), (p.get("author") or {}).get("handle", "")
            if uri in seen or not looks_like_request(rec.get("text", "")):
                continue
            seen.add(uri)
            rkey = uri.rsplit("/", 1)[-1]
            out.append(_post(source="bluesky", community="bluesky", pid=uri,
                             text_in=rec.get("text", ""),
                             url=f"https://bsky.app/profile/{handle}/post/{rkey}",
                             posted_at=(rec.get("createdAt") or "")[:19],
                             flair=q.strip('"')))
        time.sleep(GAP)
    return out


_STATE = re.compile(r'<script type="application/json" id="___INITIAL_STATE___">\s*(.*?)</script>', re.S)


def _tumblr_posts(state) -> list[dict]:
    found = []
    def walk(o):
        if isinstance(o, dict):
            if "blogName" in o and "postUrl" in o and "id" in o:
                found.append(o)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(state)
    return found


def _tumblr_text(p: dict) -> str:
    """The post's words: its ask (the question) first, then any text blocks."""
    parts = []
    for block in (p.get("content") or []) + [
            b for l in (p.get("layout") or []) for b in (l.get("blocks") or [])
            if isinstance(b, dict)]:
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts) or (p.get("summary") or "")


def fetch_tumblr(tags=TUMBLR_TAGS) -> list[dict]:
    out, seen = [], set()
    for tag in tags:
        try:
            page = _get("https://www.tumblr.com/tagged/" + urllib.parse.quote(tag)
                        + "?sort=recent", accept="text/html").decode("utf-8", "replace")
            m = _STATE.search(page)
            posts = _tumblr_posts(json.loads(m.group(1))) if m else []
        except Exception as e:
            log.info("tumblr %s: %s", tag, e)
            time.sleep(GAP)
            continue
        for p in posts:
            pid = str(p.get("id"))
            body = _tumblr_text(p)
            if pid in seen or not looks_like_request(body):
                continue
            seen.add(pid)
            ts = p.get("timestamp")
            out.append(_post(source="tumblr", community="tumblr", pid=pid,
                             text_in=body, url=p.get("postUrl") or "",
                             posted_at=(datetime.fromtimestamp(ts, timezone.utc)
                                        .strftime("%Y-%m-%dT%H:%M:%S") if ts else ""),
                             flair=tag))
        time.sleep(GAP)
    return out


def run(dry_run: bool = False) -> dict:
    import reddit_queue
    from db.session import db_session

    posts = fetch_bluesky() + fetch_tumblr()
    stats = {"fetched": len(posts), "new": 0, "answerable": 0,
             "bluesky": sum(p["source"] == "bluesky" for p in posts),
             "tumblr": sum(p["source"] == "tumblr" for p in posts)}
    if not posts:
        return stats
    with db_session() as db:
        if not dry_run:
            db.execute(text(reddit_queue.DDL))
            db.execute(text(reddit_queue.DDL_SOURCE))
            db.commit()
        known = {r[0] for r in db.execute(text(
            "SELECT id FROM reddit_posts WHERE id = ANY(:ids)"),
            {"ids": [p["id"] for p in posts]})} if not dry_run else set()
        for p in posts:
            if p["id"] in known:
                continue
            stats["new"] += 1
            read = reddit_queue._read(db, p)
            if read["works"] and not read["link_unsafe"]:
                stats["answerable"] += 1
            if dry_run:
                log.info("  %-8s %-70s works=%s%s", p["source"], p["title"][:70],
                         read["works"], "  UNSAFE" if read["link_unsafe"] else "")
                continue
            db.execute(text("""
                INSERT INTO reddit_posts
                    (id, source, subreddit, title, body, url, posted_at, flair,
                     query, works, link_unsafe, state)
                VALUES (:id, :src, :sub, :title, :body, :url,
                        CAST(NULLIF(:posted, '') AS timestamp), :flair,
                        :q, :w, :unsafe, 'new')
                ON CONFLICT (id) DO NOTHING
            """), {"id": p["id"], "src": p["source"], "sub": p["subreddit"],
                   "title": p["title"], "body": p["body"], "url": p["url"],
                   "posted": (p["posted_at"] or "").replace("T", " "),
                   "flair": p["flair"], "q": read["query"], "w": read["works"],
                   "unsafe": read["link_unsafe"]})
            db.commit()
        if not dry_run:
            from api.settings import put_setting
            put_setting(db, "outreach_sources_ran_at", str(int(time.time())))
    log.info("outreach_sources: %s", stats)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(run(dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
