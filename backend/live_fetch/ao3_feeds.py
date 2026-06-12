"""
AO3 Atom Feed Discovery
=======================
The reliable path for FRESH AO3 data from a VPS, per research:

AO3 publishes per-canonical-tag Atom feeds at:
    https://archiveofourown.org/tags/{tag_id}/feed.atom

These are NOT rate-limited/challenged the way /works/search is, because they're
the same feeds RSS readers poll. Each feed returns the ~25 most recent works for
a canonical fandom/character/relationship tag.

We also fall back to the OTW mirror domain on 525/503 origin errors:
    https://archive.transformativeworks.org

Strategy:
  1. Map fandom name -> canonical tag_id (cached; resolved by scraping the tag page once)
  2. Poll feed.atom for each tracked tag on a schedule
  3. Parse <entry> elements -> work id, title, author, updated
  4. Persist new works into the index

Etiquette (AO3-sanctioned):
  - User-agent includes "bot"
  - Delay between requests
  - Avoid weekends at volume
"""
import re
import asyncio
import logging
from datetime import datetime
import httpx

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PRIMARY = "https://archiveofourown.org"
MIRROR  = "https://archive.transformativeworks.org"

HEADERS = {
    # AO3 explicitly asks scrapers to include "bot" in the UA
    "User-Agent": "FicAtlasBot/1.0 (+https://github.com/Georgexzy/ficatlas; fanfic discovery)",
    "Accept": "application/atom+xml, application/xml, text/xml",
}

REQUEST_DELAY = 4.0   # seconds between requests — be polite


async def _get_with_fallback(client: httpx.AsyncClient, path: str) -> httpx.Response | None:
    """Try primary domain, fall back to mirror on origin errors (525/503/502)."""
    for base in (PRIMARY, MIRROR):
        try:
            r = await client.get(f"{base}{path}")
            if r.status_code == 200:
                return r
            if r.status_code in (525, 503, 502, 500):
                log.warning(f"{base}{path} -> {r.status_code}, trying fallback")
                continue
            # 429/418 = rate limited; don't hammer the mirror, just bail
            if r.status_code in (429, 418):
                log.warning(f"{base}{path} -> {r.status_code} (rate limited)")
                return None
        except Exception as e:
            log.warning(f"{base}{path} failed: {e}")
            continue
    return None


async def resolve_tag_id(client: httpx.AsyncClient, fandom_name: str) -> str | None:
    """
    Resolve a fandom/ship name to its canonical AO3 tag feed.
    AO3 tag feed URLs use the tag NAME (URL-encoded), not a numeric id, in the form:
        /tags/{Tag*Name}/feed.atom
    where spaces become *s*  and / becomes *s*... actually AO3 uses a specific escaping.
    Simplest robust approach: hit the works page for the tag and read the feed link.
    """
    # AO3 tag escaping: space -> "%20" works in practice via the tags path with the display name
    # The canonical feed link is embedded in the tag's works page <link rel="alternate" type="application/atom+xml">
    from urllib.parse import quote
    tag_path = quote(fandom_name, safe="")
    r = await _get_with_fallback(client, f"/tags/{tag_path}/works")
    if not r:
        return None
    # Look for the atom feed link
    m = re.search(r'href="[^"]*/tags/([^/"]+)/feed\.atom"', r.text)
    if m:
        return m.group(1)
    return None


def _parse_atom(xml: str) -> list[dict]:
    """Extract work entries from an AO3 Atom feed."""
    entries = []
    for entry_xml in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL):
        def tag(name):
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", entry_xml, re.DOTALL)
            return m.group(1).strip() if m else None

        link_m = re.search(r'<link[^>]*href="([^"]+)"', entry_xml)
        url = link_m.group(1) if link_m else None
        if not url:
            continue

        id_m = re.search(r"/works/(\d+)", url)
        work_id = id_m.group(1) if id_m else None
        if not work_id:
            continue

        title = tag("title") or "Untitled"
        title = re.sub(r"<[^>]+>", "", title).strip()

        # Author is in <author><name>
        author_m = re.search(r"<author>.*?<name>(.*?)</name>.*?</author>", entry_xml, re.DOTALL)
        author = author_m.group(1).strip() if author_m else "Anonymous"

        updated = tag("updated") or tag("published")
        updated_dt = None
        if updated:
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                pass

        # Summary/content holds the tags + summary as HTML
        content = tag("content") or tag("summary") or ""
        # AO3 puts fandoms/relationships/characters as text in the content
        summary_text = re.sub(r"<[^>]+>", " ", content)
        summary_text = re.sub(r"\s+", " ", summary_text).strip()[:1000]

        entries.append({
            "id": f"live_ao3_{work_id}",
            "site_id": work_id,
            "url": f"https://archiveofourown.org/works/{work_id}",
            "title": title,
            "author": author,
            "summary": summary_text or None,
            "updated_at": updated_dt.isoformat() if updated_dt else None,
            "fandoms": [], "characters": [], "relationships": [], "tags": [],
            "rating": "NR", "status": "in_progress",
            "language": "English",
            "word_count": 0, "chapter_count": 1,
            "kudos": 0, "hits": 0, "bookmarks": 0, "comments": 0,
            "warnings": [], "categories": [],
        })

    return entries


async def fetch_feed(tag_id: str, limit: int = 25) -> list[dict]:
    """Fetch and parse a single AO3 tag Atom feed."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        r = await _get_with_fallback(client, f"/tags/{tag_id}/feed.atom")
        if not r:
            return []
        return _parse_atom(r.text)[:limit]


async def poll_feeds(tag_ids: list[str]) -> list[dict]:
    """Poll multiple tag feeds with polite delays. Returns merged, deduped entries."""
    all_entries = []
    seen = set()
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for i, tag_id in enumerate(tag_ids):
            if i > 0:
                await asyncio.sleep(REQUEST_DELAY)
            r = await _get_with_fallback(client, f"/tags/{tag_id}/feed.atom")
            if not r:
                continue
            for entry in _parse_atom(r.text):
                if entry["url"] not in seen:
                    seen.add(entry["url"])
                    all_entries.append(entry)
    return all_entries
