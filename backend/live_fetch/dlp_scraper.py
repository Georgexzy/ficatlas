"""
DarkLordPotter (DLP) Library scraper.

DLP curates a list of ~1000+ vetted HP fanfics with external links to FFN/AO3/
PatronusCharm/FicWad/HPFanficArchive etc, plus DLP-internal tags for each story.
DLP itself doesn't host the text — they link out.

This module parses the library list at:
  https://forums.darklordpotter.net/pages/library-list             (HP-only)
  https://forums.darklordpotter.net/pages/library-list?corpus=other (other fandoms)

Each entry looks like:
  ### [Title by Author - Rating](dlp-thread-url)
    - [tag1](tag-url)
    - [tag2](tag-url)
    ...
  - [FFN](ffn-url)
    - [AO3](ao3-url)
    - [PatronusCharm](pc-url)
    ...

Output: list of {title, author, rating, dlp_tags, urls: {ffn, ao3, ...}, dlp_thread}.
The URLs (FFN/AO3) are what FicHub can import. DLP tags merge into the indexed story.
"""
import re
import html as _html
import logging
import httpx

log = logging.getLogger(__name__)

HP_URL    = "https://forums.darklordpotter.net/pages/library-list"
OTHER_URL = "https://forums.darklordpotter.net/pages/library-list?corpus=other"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Wayback fallback: if DLP blocks our IP, try the most recent archived snapshot.
# Wayback's CDX gives us snapshot timestamps; the timemap "id_" URL serves raw HTML
# without the Wayback header chrome, which is easier to parse.
WAYBACK_AVAILABLE = "https://archive.org/wayback/available"


# Regex for one numbered library entry.
# We capture the title-line + the following block until the next numbered heading.
# DLP renders each entry as: <li><h3><a href="THREAD_URL">TITLE - RATING</a></h3>
#   ...tag links... ...external links...</li>
_ENTRY_RE = re.compile(
    r'<li[^>]*>\s*<h3[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>(.*?)</li>',
    re.DOTALL | re.IGNORECASE,
)
# Tag link inside an entry: /tags/{slug}/
_TAG_RE = re.compile(r'<a\s+href="(/tags/[^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)
# External link inside an entry: any <a href> NOT pointing to /tags/ or /threads/
_EXT_RE = re.compile(r'<a\s+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE)

# Identify the host of an external link to label it
def _classify(url: str) -> str | None:
    u = url.lower()
    if "fanfiction.net/s/" in u: return "ffn"
    if "archiveofourown.org/works/" in u: return "ao3"
    if "patronuscharm.net" in u:          return "patronuscharm"
    if "ficwad.com" in u:                 return "ficwad"
    if "hpfanficarchive.com" in u:        return "hpfanficarchive"
    if "fanficauthors.net" in u:          return "ffa"
    if "harrypotterfanfiction.com" in u:  return "hpff"
    if "spacebattles.com" in u:           return "spacebattles"
    return None


def _parse_title_line(raw: str) -> tuple[str, str, str | None]:
    """
    DLP titles are of the form: "Title by Author - Rating" or "Title by Author [M]".
    Returns (title, author, rating).
    """
    raw = _html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()

    # Rating patterns we strip off the end: " - K+", " - T", " - M/NC-17", " [M]", " - NC-17", " (Oneshot)"
    rating = None
    m = re.search(r"\s*[\-\u2013]\s*([KTMR](?:\+|/[A-Z0-9\-]+)?|NC-?17|PG(?:-?\d+)?)\s*$", raw, re.IGNORECASE)
    if m:
        rating = m.group(1).upper()
        raw = raw[:m.start()].strip()
    else:
        m2 = re.search(r"\s*\[([A-Z0-9\-/+]+)\]\s*$", raw)
        if m2:
            rating = m2.group(1).upper()
            raw = raw[:m2.start()].strip()

    # Split "Title by Author"
    m = re.search(r"\s+by\s+(.+)$", raw, re.IGNORECASE)
    if m:
        title  = raw[:m.start()].strip()
        author = m.group(1).strip()
    else:
        title  = raw
        author = "Unknown"

    return title, author, rating


def parse_dlp_library(html_text: str) -> list[dict]:
    """Parse a DLP library-list HTML page into structured entries.

    Strategy: locate each story's <h3>...</h3> heading and treat the slice from
    that heading to the next heading (or end of document) as the entry body.
    This sidesteps the non-greedy </li> matching problem caused by nested lists.
    """
    # Find all (title-anchor href, title text, position-of-heading) tuples in order
    heading_re = re.compile(
        r'<h3[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
        re.DOTALL | re.IGNORECASE,
    )
    matches = list(heading_re.finditer(html_text))
    entries: list[dict] = []

    for i, m in enumerate(matches):
        thread_url = m.group(1).strip()
        title_html = m.group(2)
        # Skip headings that aren't story threads (e.g. site nav)
        if "/threads/" not in thread_url:
            continue

        # Body runs from end of this heading to start of next heading (or EOF)
        body_start = m.end()
        body_end   = matches[i+1].start() if i+1 < len(matches) else len(html_text)
        body = html_text[body_start:body_end]

        title, author, rating = _parse_title_line(title_html)

        # DLP tags from /tags/xxx links in the body
        dlp_tags = []
        seen_labels = set()
        for tm in _TAG_RE.finditer(body):
            label = _html.unescape(tm.group(2)).strip()
            if not label or label.lower() in seen_labels: continue
            seen_labels.add(label.lower())
            dlp_tags.append(label)

        # External links: classify FFN / AO3 / etc.
        urls: dict[str, str] = {}
        for em in _EXT_RE.finditer(body):
            href = em.group(1).strip()
            if "darklordpotter.net" in href: continue   # skip self-links
            kind = _classify(href)
            if kind and kind not in urls:
                urls[kind] = href

        entries.append({
            "title":      title,
            "author":     author,
            "rating":     rating,
            "dlp_tags":   dlp_tags,
            "urls":       urls,
            "dlp_thread": thread_url,
        })

    return entries


async def fetch_dlp_library(corpus: str = "hp", limit: int | None = None) -> list[dict]:
    """
    Fetch and parse a DLP library list page.

    corpus="hp"   → HP-only list (default)
    corpus="other" → non-HP fandoms list

    Falls back to the Wayback Machine if DLP blocks the direct request (some
    datacenter IP ranges get 403s). Returns the list of parsed entries.
    """
    url = HP_URL if corpus == "hp" else OTHER_URL

    html_text: str | None = None
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        # Try direct first
        try:
            r = await c.get(url)
            if r.status_code == 200 and len(r.text) > 5000:
                html_text = r.text
                log.info(f"DLP direct fetch OK ({len(r.text)} bytes)")
            else:
                log.warning(f"DLP direct fetch returned {r.status_code} ({len(r.text)} bytes) — trying Wayback")
        except Exception as e:
            log.warning(f"DLP direct fetch failed: {e} — trying Wayback")

        # Wayback fallback
        if not html_text:
            try:
                avail = await c.get(WAYBACK_AVAILABLE, params={"url": url})
                snap = (avail.json() or {}).get("archived_snapshots", {}).get("closest")
                if snap and snap.get("available"):
                    ts = snap["timestamp"]
                    # The "id_" suffix returns the raw archived HTML, not Wayback's framed view
                    raw_url = f"https://web.archive.org/web/{ts}id_/{url}"
                    r2 = await c.get(raw_url)
                    if r2.status_code == 200 and len(r2.text) > 5000:
                        html_text = r2.text
                        log.info(f"DLP via Wayback snapshot {ts} OK ({len(r2.text)} bytes)")
            except Exception as e:
                log.warning(f"Wayback fallback for DLP failed: {e}")

    if not html_text:
        return []

    entries = parse_dlp_library(html_text)
    log.info(f"DLP library ({corpus}): parsed {len(entries)} entries")
    return entries[:limit] if limit else entries
