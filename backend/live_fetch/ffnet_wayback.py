"""
FanFiction.net discovery via the Wayback Machine CDX API.

Why: FFN itself is hard-blocked by Cloudflare from VPS IPs. But Wayback (archive.org)
has crawled millions of FFN story pages over the years, and archive.org serves their
CDX index without Cloudflare. So we can enumerate FFN story IDs from Wayback, then
import the current text via FicHub (which solves Cloudflare on its end).

CDX API docs: https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md

Pattern:
  GET https://web.archive.org/cdx/search/cdx?url=fanfiction.net/s/*&output=json
      &from=20230101&limit=N&collapse=urlkey&fl=original,timestamp

This returns FFN story URLs (de-duplicated by urlkey) along with snapshot timestamps.
Use the `original` field to extract the story ID, then call FicHub to import current.
"""
import re
import asyncio
import logging
import httpx
from datetime import datetime

log = logging.getLogger(__name__)

CDX_BASE = "https://web.archive.org/cdx/search/cdx"
HEADERS  = {"User-Agent": "FicAtlasBot/1.0 (+fanfic discovery via Wayback)"}


def _extract_ffn_id(url: str) -> str | None:
    """Extract the story id from a FanFiction.net URL like /s/12345/1/Title."""
    m = re.search(r"fanfiction\.net/s/(\d+)", url)
    return m.group(1) if m else None


async def discover_ffn_urls(
    query: str | None = None,
    since: str = "20230101",
    limit: int = 100,
) -> list[dict]:
    """
    Find FanFiction.net story URLs via the Wayback Machine CDX API.

    Args:
      query: optional substring to look for in story slugs (e.g. "Harry-Potter").
             Wayback CDX doesn't search fulltext, only matches URL patterns.
      since: YYYYMMDD lower bound for snapshot timestamps.
      limit: max URLs to return.

    Returns: list of {url, story_id, snapshot_ts} dicts, deduplicated by story_id.
    """
    # If a query is supplied, narrow the URL pattern. Otherwise just grab any /s/* URLs.
    url_pattern = "fanfiction.net/s/*"

    params = {
        "url":      url_pattern,
        "output":   "json",
        "from":     since,
        "limit":    str(min(limit * 3, 1000)),  # over-fetch since we de-dupe
        "collapse": "urlkey",                   # one row per unique URL
        "fl":       "original,timestamp",
        "filter":   "statuscode:200",
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        try:
            r = await client.get(CDX_BASE, params=params)
            if r.status_code != 200:
                log.warning(f"Wayback CDX returned {r.status_code}")
                return []
            data = r.json()
        except Exception as e:
            log.warning(f"Wayback CDX failed: {e}")
            return []

    if not data or len(data) < 2:
        return []

    # First row is the header
    results = []
    seen_ids = set()
    for row in data[1:]:
        if not row or len(row) < 2:
            continue
        original, ts = row[0], row[1]
        story_id = _extract_ffn_id(original)
        if not story_id or story_id in seen_ids:
            continue

        # Strip query/fragment, keep only canonical /s/{id} form
        canonical = f"https://www.fanfiction.net/s/{story_id}"

        # If user gave a query, filter by URL substring (case-insensitive)
        if query and query.lower() not in original.lower():
            continue

        seen_ids.add(story_id)
        results.append({
            "url":         canonical,
            "story_id":    story_id,
            "snapshot_ts": ts,
            "original_url": original,
        })
        if len(results) >= limit:
            break

    log.info(f"Wayback CDX: found {len(results)} unique FFN URLs (query={query!r})")
    return results
PYEOF
