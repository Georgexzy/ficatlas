"""FanFiction.net metadata, fetched from the Internet Archive instead of FF.net.

The problem this solves
-----------------------
FF.net has blocked automated access since 2021 and does it with Cloudflare. Every
endpoint is shut: story pages, listings, author profiles, the Atom and RSS feeds,
and the mobile site — all eight tested from this host return the same "Just a
moment" challenge. FicHub, the one sanctioned intermediary, rate-limits us hard
and has itself reported FF.net as "fragile".

The community's documented workarounds are a human loading pages in their own
browser, or Cloudflare-evasion proxies. Neither belongs in an unattended indexer,
and the second is not something this project will do.

The way through is to stop asking FF.net at all. The Internet Archive crawls it
independently, their CDX API is public and documented, and web.archive.org is not
behind the challenge. Measured before this was written: 20,000+ successful
FF.net story captures since January 2026 — and that was the query limit, not the
ceiling. A fetched snapshot carries the full metadata line (rating, language,
genre, characters, words, reviews, favourites, follows, published and updated
dates, story id) and the chapter text.

This is the same route wayback_harvest.py already takes for AO3, for the same
reason, and the legitimacy argument is the one in that module's docstring: the
OTW's own statement on scraping names "fans backing up works to the Wayback
Machine" as an acceptable use. We read a third party's archive; FF.net serves us
nothing and pays nothing.

What it is not
--------------
Not parity with AO3. Wayback's FF.net coverage is partial and lags by however
long it takes them to recrawl, so this cannot keep FF.net as fresh as an archive
that lets us crawl it directly. It is the difference between a stale corner of
the index and no route at all.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

log = logging.getLogger(__name__)

# `id_` asks Wayback for the original bytes rather than its rewritten page, so
# the markup matches what a parser written against FF.net expects.
SNAPSHOT = "https://web.archive.org/web/{ts}id_/{url}"
CDX_URL = "http://web.archive.org/cdx/search/cdx"

# Only first chapters. A story's metadata block is identical on every chapter
# page, so fetching /s/<id>/7/ as well would spend the archive's bandwidth to
# learn nothing.
STORY_URL_RE = re.compile(r"fanfiction\.net/s/(?P<sid>\d+)/1(?:/|$)", re.IGNORECASE)


def cdx_params(since: str = "20260101", limit: int = 5000,
               resume: str | None = None) -> dict:
    """Query for recently-captured FF.net story pages.

    collapse=urlkey keeps one row per story rather than one per capture — a
    popular fic may have hundreds of snapshots and we want the newest of each.
    """
    p = {
        "url": "fanfiction.net/s/",
        "matchType": "prefix",
        "output": "json",
        "from": since,
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "fl": "timestamp,original",
        "limit": str(limit),
        "showResumeKey": "true",
    }
    if resume:
        p["resumeKey"] = resume
    return p


def story_id_from_url(url: str) -> int | None:
    m = STORY_URL_RE.search(url or "")
    return int(m.group("sid")) if m else None


def _strip(html_fragment: str | None) -> str | None:
    if not html_fragment:
        return None
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = (text.replace("&amp;", "&").replace("&#39;", "'")
                .replace("&quot;", '"').replace("&nbsp;", " ")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip() or None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        return None


def _date(value: str | None):
    """FF.net prints m/d/yyyy, and sometimes a relative form we cannot use."""
    if not value:
        return None
    # FF.net printed m-d-yy for years and switched to m/d/yyyy later; the
    # archive spans both, so a parser that only knows one silently drops the
    # publication date of every older capture — and publication date is what
    # orders a series when nothing else can.
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_story_snapshot(html_text: str, story_id: int) -> dict | None:
    """Pull metadata out of an archived FF.net story page.

    Returns None when the page is not a story — Wayback holds error pages,
    Cloudflare challenges captured mid-block, and FF.net's own "story not found"
    under perfectly ordinary 200s, and none of those should reach the database.
    """
    if not html_text or len(html_text) < 500:
        return None
    # The stats line is the anchor: no "Rated:" means this is not a story page,
    # whatever else the capture contains.
    # The block is wrapped in a <span class='xgray'> on some captures and a
    # <div> on others, and FF.net's markup has changed over the twenty years the
    # archive covers. Terminate on whichever closing tag arrives first, or the
    # end of the document.
    # A fixed window after "Rated:", tags stripped afterwards — NOT a capture
    # that stops at the first closing tag. FF.net wraps individual fields in
    # their own elements on some captures, so terminating at </span> or <div>
    # truncated the line mid-way: "Updated" survived while "Published" and
    # "Status: Complete" were cut off, losing both the publication date that
    # orders a series and the completion flag.
    stats_m = re.search(r"Rated:(?P<stats>.{0,1400})", html_text, re.S)
    if not stats_m:
        return None
    stats = _strip(stats_m.group("stats")) or ""

    title = _strip((re.search(r"<b class=['\"]xcontrast_txt['\"]>(.{0,200}?)</b>",
                              html_text, re.S) or [None, None])[1]
                   if re.search(r"<b class=['\"]xcontrast_txt['\"]>", html_text) else None)
    if not title:
        m = re.search(r"<title>(.{0,200}?),\s*a\s.{0,80}?fanfic\s*\|", html_text, re.S)
        title = _strip(m.group(1)) if m else None
    if not title:
        return None

    # Matched anywhere rather than anchored to href="/u/…": the link is absolute
    # on some captures ("http://www.fanfiction.net/u/…") and rewritten by the
    # archive on others, and anchoring missed both. Author is not optional —
    # every series detector groups by it.
    author_m = re.search(r"/u/(?P<uid>\d+)/(?P<slug>[A-Za-z0-9][A-Za-z0-9._-]{0,60})",
                         html_text)
    author = None
    if author_m:
        # The visible name is the link text; the slug is a URL-safe version of it
        # and is the reliable fallback.
        vis = re.search(r"/u/\d+/[^'\"]{0,60}['\"][^>]*>([^<]{1,80})</a>", html_text)
        author = _strip(vis.group(1)) if vis else author_m.group("slug").replace("-", " ")

    summary = None
    sm = re.search(r"<div style=['\"]margin-top:2px['\"][^>]*>(.{0,3000}?)</div>",
                   html_text, re.S)
    if sm:
        summary = _strip(sm.group(1))

    def field(pattern: str) -> str | None:
        m = re.search(pattern, stats, re.IGNORECASE)
        return m.group(1).strip() if m else None

    chapters = _int(field(r"Chapters:\s*([\d,]+)"))
    published = _date(field(r"Published:\s*([\d/-]+)"))
    updated = _date(field(r"Updated:\s*([\d/-]+)"))

    return {
        # Same shape live_fetch produces, because persist_live_results is what
        # saves both and keys on these.
        "id": f"wayback_ffnet_{story_id}",
        "site": "ffnet",
        "site_id": str(story_id),
        "url": f"https://www.fanfiction.net/s/{story_id}/1/",
        "title": title,
        "author": author,
        "summary": summary,
        "rating": field(r"Fiction\s+([KTMkTm+]+)"),
        "language": field(r"-\s*(English|Spanish|French|German|Portuguese|Italian|"
                          r"Polish|Dutch|Russian|Chinese|Japanese|Indonesian)\s*-"),
        "word_count": _int(field(r"Words:\s*([\d,]+)")),
        "chapter_count": chapters or 1,
        "reviews": _int(field(r"Reviews:\s*([\d,]+)")),
        # FF.net favourites are the closest thing it has to AO3 kudos, and that
        # is the column the rest of the app ranks on.
        "kudos": _int(field(r"Favs:\s*([\d,]+)")),
        "follows": _int(field(r"Follows:\s*([\d,]+)")),
        "published_at": published,
        "updated_at": updated or published,
        # "Complete" appears in the stats line when the author marked it so.
        # "- Complete" on modern pages, "Status: Complete" on older ones.
        "status": "complete" if re.search(r"(?:Status:\s*)?\bComplete\b", stats,
                                          re.IGNORECASE) else "unknown",
    }


# ── Fetching, on archive.org's terms ─────────────────────────────────────────
#
# The budget, backpressure handling and retry rules all come from
# wayback_harvest: it is the same host, the same rate limit and the same
# behaviour of refusing connections rather than answering 429. Sharing the
# budget object is the point — two loops each politely obeying their own budget
# would together be twice as impolite as either.

def fetch_story(story_id: int, ts: str, timeout: float = 90.0) -> dict | None:
    """Fetch one archived FF.net story page.

    Returns parsed metadata, None when the capture holds no usable story page,
    and raises wayback_harvest.Transient when archive.org asks us to back off.
    """
    import httpx

    from wayback_harvest import BACKPRESSURE, BUDGET, HEADERS, Transient, note_response

    url = f"https://www.fanfiction.net/s/{story_id}/1/"
    # Accept-Encoding: identity, because `id_` serves the ORIGINAL bytes with the
    # ORIGINAL headers. A capture taken when FF.net sent gzip carries
    # Content-Encoding: gzip while the archive may hand back a body that is
    # already decompressed, and the client then fails on a header that no longer
    # describes the payload: "Error -3 while decompressing data: incorrect header
    # check". Asking for no encoding removes the mismatch.
    #
    # Without this EVERY fetch raised DecodingError, which the loop reads as
    # archive.org applying backpressure — so it would have retried the same
    # snapshots forever and ingested nothing, while looking like a rate limit.
    headers = {**HEADERS, "Accept-Encoding": "identity"}
    BUDGET.wait()
    try:
        r = httpx.get(SNAPSHOT.format(ts=ts, url=url), headers=headers,
                      timeout=timeout, follow_redirects=True)
    except httpx.DecodingError:
        # Not backpressure, and the distinction matters: treated as Transient
        # this is retried forever, never succeeds, and looks exactly like a rate
        # limit in the logs.
        #
        # `id_` replays the ORIGINAL bytes with the ORIGINAL headers, so a
        # capture taken when FF.net sent gzip claims Content-Encoding: gzip even
        # where the stored body is not — and the client fails on a header that no
        # longer describes the payload. It happens on some captures and not
        # others. The rewritten replay carries the same metadata (stats line and
        # title survive; only links and assets are rewritten), so it is a clean
        # fallback.
        BUDGET.wait()
        try:
            r = httpx.get(SNAPSHOT.format(ts=ts, url=url).replace("id_/", "/"),
                          headers=headers, timeout=timeout, follow_redirects=True)
        except httpx.DecodingError:
            return None          # this capture is unreadable; retire it
        except httpx.RequestError as e:
            BUDGET.network_error()
            raise Transient(type(e).__name__) from e
    except httpx.RequestError as e:
        BUDGET.network_error()
        raise Transient(type(e).__name__) from e
    note_response(r.status_code, r.headers.get("Retry-After"))
    if r.status_code in BACKPRESSURE:
        raise Transient(f"HTTP {r.status_code}")
    if r.status_code != 200:
        return None
    return parse_story_snapshot(r.text, story_id)


def cdx_page(resume: str | None = None, since: str = "20260101",
             limit: int = 5000) -> tuple[list[tuple[int, str]], str | None]:
    """One page of the CDX index. Returns [(story_id, timestamp)], resume key."""
    import httpx

    from wayback_harvest import BUDGET, HEADERS, Transient

    BUDGET.wait()
    try:
        r = httpx.get(CDX_URL, params=cdx_params(since, limit, resume),
                      headers=HEADERS, timeout=180)
    except httpx.RequestError as e:
        BUDGET.network_error()
        raise Transient(type(e).__name__) from e
    if r.status_code != 200:
        raise Transient(f"cdx HTTP {r.status_code}")

    import json
    try:
        rows = json.loads(r.text or "[]")
    except ValueError:
        return [], None
    if not rows:
        return [], None

    # A resume key arrives as a blank row followed by the key.
    next_key = None
    if len(rows) >= 2 and rows[-2] == []:
        next_key = rows[-1][0] if rows[-1] else None
        rows = rows[:-2]

    out: list[tuple[int, str]] = []
    for row in rows[1:]:               # row 0 is the header
        if len(row) < 2:
            continue
        ts, original = row[0], row[1]
        sid = story_id_from_url(original)
        if sid:
            out.append((sid, ts))
    return out, next_key


def queue_ids(db, pairs: list[tuple[int, str]]) -> int:
    """Add discovered stories to the queue, newest snapshot winning."""
    from sqlalchemy import text as sql_text

    if not pairs:
        return 0
    added = 0
    for sid, ts in pairs:
        added += db.execute(sql_text("""
            INSERT INTO ffnet_wayback_queue (story_id, snapshot_ts)
            VALUES (:s, :t)
            ON CONFLICT (story_id) DO UPDATE
                SET snapshot_ts = GREATEST(ffnet_wayback_queue.snapshot_ts,
                                           EXCLUDED.snapshot_ts)
              WHERE ffnet_wayback_queue.done_at IS NULL
        """), {"s": sid, "t": ts}).rowcount or 0
    db.commit()
    return added


def next_batch(db, limit: int = 20) -> list[tuple[int, str]]:
    from sqlalchemy import text as sql_text
    return [(r[0], r[1]) for r in db.execute(sql_text(
        "SELECT story_id, snapshot_ts FROM ffnet_wayback_queue "
        "WHERE done_at IS NULL ORDER BY story_id LIMIT :l"), {"l": limit})]


def mark_done(db, story_id: int, ok: bool) -> None:
    from sqlalchemy import text as sql_text
    db.execute(sql_text("UPDATE ffnet_wayback_queue SET done_at = now(), ok = :o "
                        "WHERE story_id = :s"), {"o": ok, "s": story_id})
