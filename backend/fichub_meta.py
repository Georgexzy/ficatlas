"""
FicHub metadata client.
=======================

FicHub's `/api/v0/meta` returns full metadata for a story URL without producing
a download, and it works for FanFiction.net — which we cannot reach directly at
all, because FFN serves datacenter IPs an interactive Cloudflare challenge.

What it gives back, verified against a live FFN work:

    title, author, authorUrl, description (the summary), words, chapters,
    created, updated, status
    rawExtendedMeta: characters, genres, language, rated, status, published,
                     updated, words, chapters, favorites, follows, reviews,
                     raw_fandom, crossover

That is the same set `ffnet_enrich` reconstructs from archive.org, except it is
current rather than a 2018 snapshot, it is structured rather than parsed out of
a " - " separated line, it costs one request instead of two, and it never
misses for want of a Wayback capture.

Politeness
----------
FicHub is a small free service, so this is used sparingly and never for bulk
sweeps of millions of rows. Their robots.txt disallows the *download* paths:

    Disallow: /epub/*      /html/*      /cache/epub/*  …

`/api/v0/*` is not disallowed, and `meta` produces no file. The EPUB flow is a
different matter — `/api/v0/epub` hands back a `/cache/epub/...` URL, which is
covered by that Disallow — so full-text fetching stays on explicit user action
in the Library page and is never run from a background loop.
"""

import logging
import re
import threading
import time
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

API = "https://fichub.net/api/v0/meta"
HEADERS = {
    "User-Agent": "FicAtlas/1.0 (personal fanfiction index; +https://github.com/Georgexzy/ficatlas)",
}

# One request every 2s across every caller. FicHub is one person's server and
# is doing the Cloudflare work on our behalf; there is no rate limit published,
# which is a reason to be conservative rather than a licence not to be.
_MIN_INTERVAL = 2.0
_lock = threading.Lock()
_next_at = 0.0

_RATING = {
    "K": "general", "K+": "general", "T": "teen", "M": "mature", "MA": "explicit",
    "General Audiences": "general", "Teen And Up Audiences": "teen",
    "Mature": "mature", "Explicit": "explicit",
    "Not Rated": "not_rated",
}


def _throttle() -> None:
    global _next_at
    with _lock:
        now = time.monotonic()
        start = max(now, _next_at)
        _next_at = start + _MIN_INTERVAL
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _when(value):
    """FicHub gives ISO strings at the top level and unix seconds inside
    rawExtendedMeta; accept either."""
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _split(value) -> list[str]:
    if not value:
        return []
    parts = re.split(r",|\s&\s|/", str(value))
    return [p.strip() for p in parts if p.strip() and len(p.strip()) <= 60]


def normalise(payload: dict) -> dict:
    """FicHub's response -> the story dict shape persist_live_results expects."""
    ext = payload.get("rawExtendedMeta") or {}

    summary = payload.get("description") or None
    if summary:
        # description is HTML; the reader shows summaries as plain text.
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()[:2000] or None

    # Only trust completion when the EXTENDED block carries it, which in
    # practice means FFN. FicHub reported "ongoing" for an AO3 work that is
    # plainly 1/1 complete, so its top-level status is a guess there — and AO3
    # completion is already read authoritatively from the work page by
    # ao3_title_repair, so there is nothing to gain by second-guessing it.
    status_raw = str(ext.get("status") or "").lower()
    if status_raw.startswith("complet"):
        status = "complete"
    elif status_raw in ("ongoing", "incomplete", "in-progress", "in_progress"):
        status = "in_progress"
    else:
        status = None

    fandoms = []
    if ext.get("raw_fandom"):
        fandoms = [f.strip() for f in str(ext["raw_fandom"]).split("&") if f.strip()]

    return {
        "url":            payload.get("source"),
        "title":          (payload.get("title") or "").strip() or None,
        "author":         (payload.get("author") or "").strip() or None,
        "summary":        summary,
        "word_count":     _int(payload.get("words")) or _int(ext.get("words")),
        "chapter_count":  _int(payload.get("chapters")) or _int(ext.get("chapters")),
        "language":       (ext.get("language") or "").strip() or None,
        "rating":         _RATING.get(str(ext.get("rated") or "").strip()),
        "status":         status,
        "published_at":   _when(ext.get("published")) or _when(payload.get("created")),
        "updated_at":     _when(ext.get("updated")) or _when(payload.get("updated")),
        "fandoms":        fandoms,
        "characters":     _split(ext.get("characters")),
        "genres":         _split(ext.get("genres")),
        # FFN's favourites are the closest thing it has to kudos; follows and
        # reviews map onto the bookmark and comment columns the same way
        # ffnet_enrich already does, so ranking stays comparable across sites.
        "kudos":          _int(ext.get("favorites")),
        "bookmarks":      _int(ext.get("follows")),
        "comments":       _int(ext.get("reviews")),
        "is_crossover":   bool(ext.get("crossover")),
    }


def fetch_meta(client: httpx.Client, url: str) -> dict | None:
    """Metadata for one story URL, or None if FicHub cannot resolve it."""
    _throttle()
    try:
        r = client.get(API, params={"q": url}, timeout=90)
    except Exception as e:
        log.debug(f"fichub meta {url}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        return None
    try:
        payload = r.json()
    except ValueError:
        return None
    if not payload or payload.get("err"):
        return None
    out = normalise(payload)
    return out if out.get("title") and out.get("url") else None
