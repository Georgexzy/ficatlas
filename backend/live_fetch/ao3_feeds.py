"""
AO3 Atom Feed Discovery — production version.

Critical detail: AO3 uses NON-STANDARD URL escaping for tag names:
  period (.)   → *d*
  slash  (/)   → *s*
  amp    (&)   → *a*
  (space stays as %20)

So "Harry Potter - J. K. Rowling" must be encoded as:
    Harry%20Potter%20-%20J*d*%20K*d*%20Rowling

Feeds live at /tags/{NUMERIC_ID}/feed.atom (the numeric form), but AO3 also
accepts /tags/{escaped_name}/feed.atom which 302-redirects to the numeric URL.
We resolve numeric IDs by fetching the tag's works page and pulling the feed
link from <link rel="alternate" type="application/atom+xml" href="...">.

Etiquette (AO3-sanctioned):
  - User-agent includes "bot"
  - Polite delays between requests
  - Mirror fallback on 525/503 origin errors
"""
import re
import asyncio
import logging
from datetime import datetime
import httpx

log = logging.getLogger(__name__)

PRIMARY = "https://archiveofourown.org"
MIRROR  = "https://archive.transformativeworks.org"

HEADERS = {
    "User-Agent": "FicAtlasBot/1.0 (+https://github.com/Georgexzy/ficatlas; fanfic discovery)",
    "Accept": "application/atom+xml, application/xml, text/xml, text/html",
}

REQUEST_DELAY = 4.0


def ao3_escape(name: str) -> str:
    """Apply AO3's quirky URL escaping: . → *d*, / → *s*, & → *a*, space → %20"""
    s = name.strip()
    # Apply substitutions in order; do NOT urlencode the * characters themselves
    s = s.replace("&", "*a*")
    s = s.replace(".", "*d*")
    s = s.replace("/", "*s*")
    # URL-encode spaces and anything else risky, but leave * intact
    out = []
    for ch in s:
        if ch == "*" or ch == "%":
            out.append(ch)
        elif ch == " ":
            out.append("%20")
        elif ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append(f"%{ord(ch):02X}")
    return "".join(out)


async def _get_with_fallback(client: httpx.AsyncClient, path: str) -> httpx.Response | None:
    """Try primary, fall back to mirror on origin errors. Return None on hard failure."""
    last_status = None
    for base in (PRIMARY, MIRROR):
        try:
            r = await client.get(f"{base}{path}")
            last_status = r.status_code
            if r.status_code == 200:
                return r
            if r.status_code in (525, 503, 502, 500):
                log.warning(f"{base}{path} → {r.status_code}, trying fallback")
                continue
            if r.status_code in (429, 418):
                log.warning(f"{base}{path} → {r.status_code} (rate limited)")
                return None
            # 404 from primary: the mirror may have different routing; try once
            if r.status_code == 404 and base == PRIMARY:
                log.info(f"{base}{path} → 404, trying mirror")
                continue
            log.warning(f"{base}{path} → {r.status_code}")
        except Exception as e:
            log.warning(f"{base}{path} failed: {e}")
            continue
    log.warning(f"All fallbacks failed for {path} (last status: {last_status})")
    return None


async def resolve_tag_id(client: httpx.AsyncClient, fandom_name: str) -> str | None:
    """
    Resolve a fandom name to its AO3 feed identifier.
    Strategy: fetch the tag's works page using AO3's escape rules, then extract the
    feed link from the page HTML. AO3 feeds use a numeric tag id in the path.
    Returns the numeric id (string) on success, else None.
    """
    escaped = ao3_escape(fandom_name)

    # First try the works page (preferred — gives us the feed link directly)
    for path in (f"/tags/{escaped}/works", f"/tags/{escaped}"):
        r = await _get_with_fallback(client, path)
        if not r:
            continue
        # Look for the atom feed link
        m = re.search(r'href="[^"]*/tags/(\d+)/feed\.atom"', r.text)
        if m:
            log.info(f"Resolved '{fandom_name}' → tag id {m.group(1)}")
            return m.group(1)
        # Fallback: the page sometimes uses the escaped name in the feed link
        m = re.search(r'href="[^"]*/tags/([^/"]+)/feed\.atom"', r.text)
        if m:
            log.info(f"Resolved '{fandom_name}' → tag {m.group(1)}")
            return m.group(1)

    log.warning(f"Couldn't resolve tag id for '{fandom_name}' (escaped: {escaped})")
    return None


def _parse_atom(xml: str) -> list[dict]:
    """Extract work entries from an AO3 Atom feed.

    AO3's <content type="html"> contains the work blurb as HTML-entity-encoded text.
    We decode entities, parse the inner HTML, and pull out summary, fandoms,
    characters, relationships, tags, rating, warnings, categories, word count etc.
    """
    import html as _html

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
        title = _html.unescape(re.sub(r"<[^>]+>", "", title)).strip()

        # Author: AO3 puts it in <author><name>
        author_m = re.search(r"<author>.*?<name>(.*?)</name>.*?</author>", entry_xml, re.DOTALL)
        author = _html.unescape(re.sub(r"<[^>]+>", "", author_m.group(1))).strip() if author_m else "Anonymous"

        updated = tag("updated") or tag("published")
        updated_dt = None
        if updated:
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                pass

        # ── Parse the <content> blurb (HTML-entity-encoded) ──
        raw_content = tag("content") or tag("summary") or ""
        # Decode the entity-encoded HTML AO3 wraps the blurb in
        decoded = _html.unescape(raw_content)

        # Extract structured fields from list items: "<li>Fandoms: <a>X</a>, <a>Y</a></li>"
        def collect_field(label: str) -> list[str]:
            """Pull all tag values from a <li>{label}: ...</li> block."""
            m = re.search(
                rf"<li>\s*{re.escape(label)}\s*:\s*(.*?)</li>",
                decoded, re.IGNORECASE | re.DOTALL,
            )
            if not m:
                return []
            block = m.group(1)
            # Each value is the text inside an <a class="tag"> ... </a>
            vals = re.findall(r"<a[^>]*class=\"tag\"[^>]*>(.*?)</a>", block, re.DOTALL)
            return [_html.unescape(re.sub(r"<[^>]+>", "", v)).strip() for v in vals if v.strip()]

        fandoms       = collect_field("Fandoms")
        ratings_l     = collect_field("Rating")
        warnings      = collect_field("Warnings") or collect_field("Archive Warnings")
        categories    = collect_field("Categories") or collect_field("Category")
        characters    = collect_field("Characters") or collect_field("Character")
        relationships = collect_field("Relationships") or collect_field("Relationship")
        add_tags      = collect_field("Additional Tags")
        all_tags      = relationships + characters + add_tags

        # Rating: take first if present
        rating_map = {
            "General Audiences":      "G",
            "Teen And Up Audiences":  "T",
            "Mature":                 "M",
            "Explicit":               "E",
            "Not Rated":              "NR",
        }
        rating = "NR"
        if ratings_l:
            rating = rating_map.get(ratings_l[0], "NR")

        # Word count + chapter count from "Words: 1234, Chapters: 1/?,"
        wc_m = re.search(r"Words:\s*([\d,]+)", decoded)
        word_count = int(wc_m.group(1).replace(",", "")) if wc_m else 0

        ch_m = re.search(r"Chapters:\s*(\d+)\s*/\s*([\d?]+)", decoded)
        chapter_count = int(ch_m.group(1)) if ch_m else 1
        chapter_count_total = None
        if ch_m and ch_m.group(2).isdigit():
            chapter_count_total = int(ch_m.group(2))
        status = "complete" if (chapter_count_total and chapter_count == chapter_count_total) else "in_progress"

        # Language
        lang_m = re.search(r"Language:\s*([A-Za-z]+)", decoded)
        language = lang_m.group(1) if lang_m else "English"

        # Summary: take the first <p>...</p> AFTER stripping the "by AUTHOR" paragraph,
        # but BEFORE the "Words: ..." paragraph. AO3 feeds wrap actual summary in <blockquote> sometimes,
        # otherwise it's just a <p> with the user's summary text.
        summary_text: str | None = None
        # Strategy: find all <p>...</p> blocks, drop the "by X" and "Words:..." ones, join the rest
        paras = re.findall(r"<p[^>]*>(.*?)</p>", decoded, re.DOTALL)
        cleaned_paras = []
        for p in paras:
            text = _html.unescape(re.sub(r"<[^>]+>", "", p)).strip()
            if not text: continue
            if text.startswith("by ") and len(text) < 100: continue       # author byline
            if re.match(r"^Words:\s*[\d,]", text): continue                # stats line
            cleaned_paras.append(text)
        if cleaned_paras:
            summary_text = "\n\n".join(cleaned_paras)[:2000]

        entries.append({
            "id": f"live_ao3_{work_id}",
            "site_id": work_id,
            "url": f"https://archiveofourown.org/works/{work_id}",
            "title": title,
            "author": author,
            "summary": summary_text,
            "updated_at": updated_dt.isoformat() if updated_dt else None,
            "fandoms":       fandoms,
            "characters":    characters,
            "relationships": relationships,
            "tags":          all_tags,
            "rating":        rating,
            "status":        status,
            "language":      language,
            "word_count":    word_count,
            "chapter_count": chapter_count,
            "chapter_count_total": chapter_count_total,
            "kudos": 0, "hits": 0, "bookmarks": 0, "comments": 0,
            "warnings":   warnings,
            "categories": categories,
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


def filter_entries(
    entries: list[dict],
    min_words: int | None = None,
    max_words: int | None = None,
    complete_only: bool = False,
    ratings: list[str] | None = None,
) -> list[dict]:
    """Post-filter feed entries by word count, completion, and rating.

    Note: AO3 feeds return the 25 newest works for a canonical tag with no
    server-side filtering. Tight filters may yield 0–3 results out of 25.
    For larger filtered sets, poll multiple narrower tags (per ship/character).
    """
    out = []
    for e in entries:
        wc = e.get("word_count") or 0
        if min_words is not None and wc < min_words:
            continue
        if max_words is not None and wc > max_words:
            continue
        if complete_only and e.get("status") != "complete":
            continue
        if ratings and e.get("rating") not in ratings:
            continue
        out.append(e)
    return out
