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
import os
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
_MIN_INTERVAL = 3.0
_lock = threading.Lock()
_next_at = 0.0
# When FicHub answers 429 (it throttles the shared IP), we stop requesting until
# this moment so the cooldown can pass instead of hammering a service that is
# telling us to stop. Without this the background FF.net enrichment loop kept
# firing into the throttle at the base interval for hours, keeping the whole IP
# blocked — and blocking user imports, which share the same budget.
_cooldown_until = 0.0
_consecutive_throttles = 0
# Default lives on the host bind-mount (./backend:/app) so it survives BOTH a
# container restart and a `docker compose up` recreate, which a container-local
# path would not. Override with FICHUB_COOLDOWN_FILE in the environment.
_COOLDOWN_FILE = os.environ.get(
    "FICHUB_COOLDOWN_FILE",
    "/app/.fichub_cooldown",
)


def _load_persisted_cooldown() -> None:
    """Restore a cooldown saved before a restart.

    The worker is a long-running process, but it can crash or be recreated. A
    restart would otherwise clear `_cooldown_until` and immediately re-hammer a
    service that throttled us seconds ago — undoing the whole point of backing
    off. Reading the persisted value means a restart resumes the pause instead
    of burning the IP again.
    """
    global _cooldown_until, _consecutive_throttles
    try:
        with open(_COOLDOWN_FILE, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw:
            return
        parts = raw.split()
        if len(parts) == 2:
            expires_at = float(parts[0])
            _consecutive_throttles = int(parts[1])
        else:
            expires_at = float(parts[0])
        if expires_at > time.monotonic():
            _cooldown_until = expires_at
            log.info(f"resumed FicHub cooldown "
                     f"({expires_at - time.monotonic():.0f}s left) from restart")
    except (OSError, ValueError, IndexError):
        pass


def _persist_cooldown() -> None:
    try:
        os.makedirs(os.path.dirname(_COOLDOWN_FILE), exist_ok=True)
        with open(_COOLDOWN_FILE, "w", encoding="utf-8") as fh:
            fh.write(f"{_cooldown_until:.0f} {_consecutive_throttles}\n")
    except OSError:
        pass


_load_persisted_cooldown()

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
        start = max(now, _next_at, _cooldown_until)
        _next_at = start + _MIN_INTERVAL
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _note_throttled(retry_after: float | None = None) -> None:
    """Record a FicHub throttle so _throttle() pauses past its cooldown."""
    global _cooldown_until, _consecutive_throttles
    _consecutive_throttles += 1
    # Wait the retry-after plus a buffer so we do not re-fire the instant the
    # window ends and trip it again. If FicHub did not say, or we keep getting
    # throttled, escalate so repeated offenders wait much longer.
    if retry_after:
        pause = float(retry_after) + 15.0
    elif _consecutive_throttles > 2:
        pause = 600.0
    else:
        pause = 60.0
    pause = min(max(pause, 10.0), 3600.0)
    with _lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + pause)
    _persist_cooldown()


def _clear_throttle() -> None:
    """After a clean 200, relax the escalation so a later blip is not over-punished."""
    global _consecutive_throttles
    if _consecutive_throttles > 0:
        _consecutive_throttles = 0
        _persist_cooldown()


def throttled() -> bool:
    """True while FicHub is throttling the shared IP.

    Background bulk consumers (the DLP import loop, FFnet enrichment fallback)
    check this and yield entirely rather than firing one request per cooldown
    window. Firing through a throttle keeps re-refreshing the cooldown, so it
    never clears and a *user* import — which shares the same IP budget — stays
    blocked too. Yielding lets the pause expire cleanly.
    """
    return time.monotonic() < _cooldown_until


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
    # A 429 is FicHub telling the whole IP to slow down. Don't just fail this
    # one request — pause the caller so it stops hammering and the cooldown can
    # clear (see _cooldown_until). Without this the background enrich loop kept
    # requesting at the base interval through a throttle, keeping us blocked.
    if r.status_code in (429, 502, 503):
        retry = r.headers.get("retry-after")
        try:
            _note_throttled(float(retry) if retry else None)
        except (TypeError, ValueError):
            _note_throttled()
        return None
    if r.status_code != 200:
        return None
    _clear_throttle()
    try:
        payload = r.json()
    except ValueError:
        return None
    if not payload or payload.get("err"):
        return None
    out = normalise(payload)
    return out if out.get("title") and out.get("url") else None
