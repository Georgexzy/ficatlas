"""More places a fandom says "read this", feeding the same `rec_mentions` table.

    docker exec ficatlas-worker-1 python rec_sources.py --dry-run
    docker exec ficatlas-worker-1 python rec_sources.py [--only ao3|bluesky|tumblr]

Why
---
"Being recommended lately" on a fandom hub showed on 8 of 5,663 hubs
(measured 2026-10-06), because its only source was ten subreddits, most of
them Harry Potter or A Song of Ice and Fire. Naruto had 71 people recommending
things and no work three of them agreed on. The block's bar (`min_people_for`
in api/hubs.py) is right; what was missing was people.

Three sources, each read as a list of (person, work, when):

AO3 rec bookmarks
    AO3 lets a reader tick "Rec" on a bookmark: an explicit, public, dated "I
    recommend this", by a named account. A fandom's rec bookmarks, newest
    first, are exactly "what this fandom is recommending lately" -- for every
    fandom AO3 carries, not just the ones with a subreddit. Each result is a
    work with its most recent bookmarks listed under it, bookmarker and date
    on each, so one page is up to ~100 dated recommendations. The bookmark
    list itself is not stored, only who/what/when. Paced through ao3_budget,
    the same shared budget as every other AO3 request in this project.

Bluesky
    Public search API (no login), restricted to posts LINKING an archive.
    Links come from the post's facets and embed, never its text: Bluesky
    shortens long URLs in the visible text ("archiveofourown.org/works/53..."),
    and reading the text would match a truncated id to somebody else's work.

Tumblr
    The public tag pages for fic-rec tags. Original posts only -- a reblog is
    a rec list passing between people, and counting each reblog as a new
    recommender would let one viral list clear any bar.

A recommendation is not self-promotion
--------------------------------------
On Bluesky especially, most posts linking AO3 are authors announcing their own
work ("self-rec", "new chapter is up", "by me"). That is not a recommendation,
and counting it would let any author put their own fic on a hub. `_SELF` drops
those. It is deliberately broad: a missed recommendation costs one mention, a
counted advert puts the wrong work on a page strangers read.

Identities are prefixed by source ("bsky:", "tumblr:", "ao3:") so a Reddit
user and an AO3 user who happen to share a name are two people, and the
community column says where it came from, which the hub prints.
"""

import argparse
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402
from work_lookup import resolve  # noqa: E402

log = logging.getLogger("rec_sources")

UA = "FicAtlas/1.0 (fanfiction index; +https://ficatlas.com)"
GAP = 1.5                       # seconds between Bluesky/Tumblr requests

_WORK = re.compile(r"archiveofourown\.org/works/(\d+)|fanfiction\.net/s/(\d+)", re.I)

# Self-promotion and update notices. Checked on the post's own words.
_SELF = re.compile(
    r"self[- ]?rec|\bby me\b|\bmy (?:own )?(?:new |latest |first |old |next )?"
    r"(?:fic|fics|story|stories|work|works|oneshot|one[- ]shot|series|chapter|wip)\b"
    r"|\bi (?:wrote|posted|finished|updated|published|uploaded|just posted)\b"
    r"|\bi(?:'ve| have) (?:written|posted|updated|finished)\b"
    r"|new chapter|chapter \d+ (?:is )?(?:up|out|posted|live)|\bnow up\b|\bout now\b"
    r"|just (?:posted|updated|uploaded)|\bupdated?!|#(?:newfic|ficupdate|selfpromo|wip|writeblr)\b"
    r"|\b(?:fics?|stor(?:y|ies)|works?|wips?) of mine\b|\bfrom me\b"
    r"|cross[- ]?posted|\bcommission|\bpatreon\b|\bko-?fi\b|promoter",
    re.I)
# What a recommendation says. Required on Bluesky, where linking a work is
# mostly authors; Tumblr's rec tags already say it.
_REC = re.compile(
    r"\brecs?\b|\brecommend|\bgo read\b|\byou should read\b|\bread this\b|#?ficrec"
    r"|\bfav(?:ou?rite)? fics?\b|\bobsessed with\b|\bi(?:'m| am) reading\b|\bjust (?:read|finished)\b"
    r"|\bcurrently reading\b|\bif you like\b|\bmust[- ]read\b|\bmasterpiece\b",
    re.I)


def _get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def store(db, *, source: str, community: str, thread_id: str, comment_id: str,
          recommender: str, when: datetime, site: str, site_id: str) -> int:
    """One mention, resolved to an indexed work where possible. Idempotent."""
    found = resolve(db, site, site_id)
    res = db.execute(text("""
        INSERT INTO rec_mentions
            (source, community, thread_id, comment_id, recommender,
             mentioned_at, story_id, site, site_id)
        VALUES (:src, :c, :t, :cid, :who, :at, :sid, :site, :siteid)
        ON CONFLICT (comment_id, site, site_id) DO NOTHING
    """), {"src": source, "c": community, "t": thread_id, "cid": comment_id,
           "who": recommender, "at": when, "sid": str(found) if found else None,
           "site": site, "siteid": site_id})
    return res.rowcount or 0


def _links(blob: str) -> list[tuple[str, str]]:
    out, seen = [], set()
    for m in _WORK.finditer(blob):
        key = ("ffnet", m.group(2)) if m.group(2) else ("ao3", m.group(1))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def is_recommendation(words: str, need_rec_word: bool) -> bool:
    if _SELF.search(words or ""):
        return False
    return bool(_REC.search(words or "")) if need_rec_word else True


# ── AO3 rec bookmarks ───────────────────────────────────────────────────────

AO3 = "https://archiveofourown.org"
AO3_FANDOMS = int(os.getenv("REC_AO3_FANDOMS", "300"))      # largest N hubs
AO3_PER_PASS = int(os.getenv("REC_AO3_PER_PASS", "15"))     # fandoms per pass
# One page. AO3 answers page 2+ of this search with a 503 (measured on every
# fandom tried, 2026-10-06), and page 1 already holds up to ~100 dated rec
# bookmarks -- for the biggest fandom that is about a day's worth, and the
# largest fandoms are visited daily.
AO3_PAGES = int(os.getenv("REC_AO3_PAGES", "1"))
_ITEM = re.compile(r'href="/(works|external_works)/(\d+)"')
_USER = re.compile(r'<li class="user short blurb group[^"]*".*?</li>', re.S)
_BYLINE = re.compile(r'Bookmarked by\s*<a href="/users/([^/"]+)/pseuds/[^"]*">')
_DATE = re.compile(r'<p class="datetime">([^<]+)</p>')
_EXTERNAL_URL = re.compile(r'<a href="(https?://[^"]+)"[^>]*>[^<]*</a>\s*by', re.S)

STATE_DDL = """
CREATE TABLE IF NOT EXISTS rec_ao3_state (
    slug        text PRIMARY KEY,
    tag         text,
    newest      date,
    checked_at  timestamptz,
    found       integer NOT NULL DEFAULT 0
)
"""


def ao3_tag_id(name: str) -> str:
    """AO3's escaping for a tag in a URL path or tag_id."""
    for a, b in (("/", "*s*"), (".", "*d*"), ("&", "*a*"), ("#", "*h*"), ("?", "*q*")):
        name = name.replace(a, b)
    return name


def parse_rec_page(page: str) -> list[dict]:
    """Every rec bookmark on one results page: who, which work, which day."""
    out = []
    # Each work's blurb opens with its own `<li id="bookmark_...">`; the
    # bookmarks listed under it are `<li class="user ...">` with no id.
    for blurb in page.split('<li id="bookmark_')[1:]:
        item = _ITEM.search(blurb)
        if not item:
            continue
        kind, wid = item.groups()
        if kind == "works":
            site, sid = "ao3", wid
        else:
            # A bookmark of a work hosted elsewhere: only useful if it is an
            # FF.net or AO3 link this index can resolve.
            ext = _links(blurb)
            if not ext:
                continue
            site, sid = ext[0]
        for li in _USER.findall(blurb):
            if 'class="rec"' not in li:
                continue
            who, day = _BYLINE.search(li), _DATE.search(li)
            if not (who and day):
                continue
            try:
                when = datetime.strptime(day.group(1).strip(), "%d %b %Y").replace(
                    hour=12, tzinfo=timezone.utc)
            except ValueError:
                continue
            out.append({"who": urllib.parse.unquote(who.group(1)), "when": when,
                        "site": site, "site_id": sid, "item": f"{kind}/{wid}"})
    return out


def _ao3_unescape(name: str) -> str:
    for a, b in (("*s*", "/"), ("*d*", "."), ("*a*", "&"), ("*h*", "#"), ("*q*", "?")):
        name = name.replace(a, b)
    return name


def _ao3_page(tag: str, page: int) -> tuple[int, str, str]:
    """One page of a fandom's rec bookmarks, newest first -> (status, html, tag).

    A tag that is a SYNONYM ("Naruto") redirects to its canonical tag's plain
    bookmarks page and the redirect drops the query string -- the same trap as
    AO3's chapter redirect dropping view_adult. Following it reads unfiltered,
    unsorted bookmarks. So the redirect is not followed: the canonical name is
    read out of it and the request is made again with that, and the caller
    stores it so the next visit asks directly.
    """
    import httpx
    import ao3_budget
    retried = False
    for _ in range(3):
        url = (f"{AO3}/bookmarks?" + urllib.parse.urlencode({
            "bookmark_search[rec]": "1",
            "bookmark_search[sort_column]": "created_at",
            "tag_id": ao3_tag_id(tag), "page": page}))
        ao3_budget.wait()
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=False, timeout=60)
        ao3_budget.note_response(r.status_code, r.headers.get("retry-after"))
        m = re.search(r"/tags/([^/?]+)/", r.headers.get("location", ""))
        if r.status_code in (301, 302) and m:
            tag = _ao3_unescape(urllib.parse.unquote(m.group(1)))
            continue
        # AO3's bookmark search 503s now and then and answers the same
        # request moments later (measured). One patient retry, then give up
        # on this fandom until its next turn.
        if r.status_code == 503 and not retried:
            retried = True
            time.sleep(30)
            continue
        return r.status_code, r.text, tag
    return 0, "", tag


def _due_fandoms(db, limit: int) -> list[tuple[str, list[str], str | None, object]]:
    return db.execute(text("""
        WITH ranked AS (
            SELECT slug, variants, row_number() OVER (ORDER BY work_count DESC) AS rk
              FROM fandom_hubs ORDER BY work_count DESC LIMIT :n)
        SELECT r.slug, r.variants, s.tag, s.newest
          FROM ranked r LEFT JOIN rec_ao3_state s USING (slug)
         WHERE s.checked_at IS NULL
            OR s.checked_at < now() - CASE WHEN r.rk <= 50 THEN interval '20 hours'
                                           ELSE interval '70 hours' END
         ORDER BY s.checked_at NULLS FIRST, r.rk
         LIMIT :lim
    """), {"n": AO3_FANDOMS, "lim": limit}).fetchall()


def _candidate_tags(db, variants: list[str]) -> list[str]:
    """Variants, most-used first: the canonical AO3 tag is the big one."""
    rows = db.execute(text("""
        SELECT value FROM facets WHERE kind IN ('fandom_ao3', 'fandom')
           AND value = ANY(:v) GROUP BY value ORDER BY max(count) DESC
    """), {"v": variants}).scalars().all()
    return list(rows) + [v for v in variants if v not in rows]


def run_ao3(dry_run: bool = False, fandoms: int = AO3_PER_PASS) -> dict:
    stored = seen = 0
    with db_session() as db:
        db.execute(text(STATE_DDL))
        db.commit()
        for slug, variants, tag, newest in _due_fandoms(db, fandoms):
            tags = [tag] if tag else _candidate_tags(db, list(variants or []))[:3]
            got, used, top = [], None, None
            for t in tags:
                for page in range(1, AO3_PAGES + 1):
                    try:
                        code, body, t = _ao3_page(t, page)
                    except Exception as e:
                        log.info("ao3 recs %s p%d: %s", slug, page, e)
                        break
                    if code != 200:
                        log.info("ao3 recs %s (%s) p%d: HTTP %d", slug, t, page, code)
                        break
                    rows = parse_rec_page(body)
                    if not rows:
                        break
                    used = t
                    got.extend(rows)
                    page_top = max(r["when"].date() for r in rows)
                    top = max(top, page_top) if top else page_top
                    # Sorted by most recent bookmark: once a whole page is at or
                    # before what the last visit saw, the rest has been read.
                    if newest and page_top < newest:
                        break
                if used:
                    break
            seen += len(got)
            n = 0
            if not dry_run:
                for r in got:
                    n += store(db, source="ao3_bookmarks", community="ao3_recs",
                               thread_id=f"ao3rec:{slug}",
                               comment_id=f"ao3rec:{r['who']}:{r['item']}",
                               recommender="ao3:" + r["who"].lower(), when=r["when"],
                               site=r["site"], site_id=r["site_id"])
                db.execute(text("""
                    INSERT INTO rec_ao3_state (slug, tag, newest, checked_at, found)
                    VALUES (:s, :t, :n, now(), :f)
                    ON CONFLICT (slug) DO UPDATE SET
                        tag = COALESCE(EXCLUDED.tag, rec_ao3_state.tag),
                        newest = GREATEST(EXCLUDED.newest, rec_ao3_state.newest),
                        checked_at = now(), found = rec_ao3_state.found + EXCLUDED.found
                """), {"s": slug, "t": used, "n": top, "f": n})
                db.commit()
            stored += n
            log.info("ao3 recs %s: %d rec bookmarks read, %d new", slug, len(got), n)
    return {"read": seen, "new": stored}


# ── Bluesky ─────────────────────────────────────────────────────────────────

BSKY = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BSKY_SEARCHES = [
    {"q": "rec", "domain": "archiveofourown.org"},
    {"q": "recommend", "domain": "archiveofourown.org"},
    {"q": "read", "domain": "archiveofourown.org"},
    {"q": "rec", "domain": "fanfiction.net"},
    {"q": "#ficrec"},
    {"q": "fic rec"},
]
BSKY_PAGES = int(os.getenv("REC_BSKY_PAGES", "3"))


def _bsky_links(post: dict) -> list[tuple[str, str]]:
    """Archive links from facets and the embed -- never the visible text."""
    rec = post.get("record") or {}
    uris = []
    for f in rec.get("facets") or []:
        for feat in f.get("features") or []:
            if feat.get("uri"):
                uris.append(feat["uri"])
    for emb in (rec.get("embed") or {}, (post.get("embed") or {})):
        ext = emb.get("external") or {}
        if ext.get("uri"):
            uris.append(ext["uri"])
    return _links(" ".join(uris))


def bsky_mentions(posts: list[dict]) -> list[dict]:
    out = []
    for p in posts:
        rec = p.get("record") or {}
        words = rec.get("text") or ""
        if not is_recommendation(words, need_rec_word=True):
            continue
        handle = ((p.get("author") or {}).get("handle") or "").lower()
        try:
            when = datetime.fromisoformat((rec.get("createdAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        for site, sid in _bsky_links(p):
            out.append({"cid": f"bsky:{p.get('uri')}", "who": "bsky:" + handle,
                        "when": when, "site": site, "site_id": sid})
    return out


def run_bluesky(dry_run: bool = False) -> dict:
    found = stored = 0
    with db_session() as db:
        for s in BSKY_SEARCHES:
            cursor = None
            for _ in range(BSKY_PAGES):
                params = dict(s, sort="latest", limit=100)
                if cursor:
                    params["cursor"] = cursor
                try:
                    d = json.loads(_get(BSKY + "?" + urllib.parse.urlencode(params)))
                except Exception as e:
                    log.info("bluesky %s: %s", s, e)
                    break
                ms = bsky_mentions(d.get("posts") or [])
                found += len(ms)
                if not dry_run:
                    new = sum(store(db, source="bluesky", community="bluesky",
                                    thread_id=m["cid"], comment_id=m["cid"],
                                    recommender=m["who"], when=m["when"],
                                    site=m["site"], site_id=m["site_id"]) for m in ms)
                    db.commit()
                    stored += new
                    if ms and new == 0:
                        break           # reached what an earlier pass read
                cursor = d.get("cursor")
                time.sleep(GAP)
                if not cursor:
                    break
    log.info("bluesky: %d recommendations seen, %d new", found, stored)
    return {"read": found, "new": stored}


# ── Tumblr ──────────────────────────────────────────────────────────────────

TUMBLR_TAGS = ["fic recs", "fic rec", "ao3 recs", "fanfic recs", "ao3 rec",
               "fanfiction recs", "fic rec list", "recommended fics"]
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


def tumblr_mentions(posts: list[dict]) -> list[dict]:
    out = []
    for p in posts:
        if p.get("trail"):              # a reblog: someone else's list
            continue
        content = json.dumps(p.get("content") or [])
        words = " ".join(b.get("text", "") for b in (p.get("content") or [])
                         if isinstance(b, dict))
        if not is_recommendation(words, need_rec_word=False):
            continue
        ts = p.get("timestamp")
        if not ts:
            continue
        blog = (p.get("blogName") or "").lower()
        for site, sid in _links(html.unescape(urllib.parse.unquote(content))):
            out.append({"cid": f"tumblr:{p.get('id')}", "who": "tumblr:" + blog,
                        "when": datetime.fromtimestamp(ts, timezone.utc),
                        "site": site, "site_id": sid})
    return out


def run_tumblr(dry_run: bool = False) -> dict:
    found = stored = 0
    with db_session() as db:
        for tag in TUMBLR_TAGS:
            try:
                page = _get("https://www.tumblr.com/tagged/" + urllib.parse.quote(tag)
                            + "?sort=recent", accept="text/html").decode("utf-8", "replace")
                m = _STATE.search(page)
                posts = _tumblr_posts(json.loads(m.group(1))) if m else []
            except Exception as e:
                log.info("tumblr %s: %s", tag, e)
                time.sleep(GAP)
                continue
            ms = tumblr_mentions(posts)
            found += len(ms)
            if not dry_run:
                stored += sum(store(db, source="tumblr", community="tumblr",
                                    thread_id=m["cid"], comment_id=m["cid"],
                                    recommender=m["who"], when=m["when"],
                                    site=m["site"], site_id=m["site_id"]) for m in ms)
                db.commit()
            time.sleep(GAP)
    log.info("tumblr: %d recommendations seen, %d new", found, stored)
    return {"read": found, "new": stored}


def run(dry_run: bool = False, only: str | None = None) -> dict:
    """One pass of every source. One failing never stops the others."""
    out = {}
    for name, fn in (("bluesky", run_bluesky), ("tumblr", run_tumblr), ("ao3", run_ao3)):
        if only and only != name:
            continue
        try:
            out[name] = fn(dry_run=dry_run)
        except Exception:
            log.warning("rec source %s failed", name, exc_info=True)
            out[name] = {"error": True}
    if not dry_run:
        try:
            with db_session() as db:
                db.execute(text("""
                    INSERT INTO app_settings (key, value) VALUES ('rec_sources_ran_at', now()::text)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """))
                db.commit()
        except Exception:
            log.debug("could not record rec_sources_ran_at", exc_info=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["ao3", "bluesky", "tumblr"])
    ap.add_argument("--fandoms", type=int, help="AO3: fandoms this pass")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.only == "ao3" and a.fandoms:
        print(run_ao3(dry_run=a.dry_run, fandoms=a.fandoms))
        return 0
    print(run(dry_run=a.dry_run, only=a.only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
