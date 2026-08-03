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


# Each story is wrapped in <li id="story-NNN" class="discussionListItem">…</li>
# Inside: <h3 class="title"><a href="threads/...">TITLE - RATING</a></h3>
#         tagBlock with <a class="tag">…</a> entries
#         lastPost block with external <a href="http(s)://…">FFN/AO3/etc</a> links
_ITEM_RE = re.compile(
    r'<li[^>]+id="story-\d+"[^>]+class="[^"]*discussionListItem[^"]*"[^>]*>',
    re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r'<h3[^>]+class="[^"]*title[^"]*"[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
# Tag link: <a class="tag" href="tags/xxx/"> ...label... </a>  (label has nested span)
_TAG_RE = re.compile(
    r'<a[^>]+href="(tags/[^"]+|/tags/[^"]+)"[^>]+class="[^"]*tag[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
# External link: any <a href="http(s)://..."> inside the item
_EXT_RE = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)


# Identify the host of an external link to label it
# Trailing junk cutoff. DLP posts contain hand-written BBCode, and a malformed
# link there renders as an <a href> with the rest of the broken tag glued on:
#
#     http://archiveofourown.org/works/680944&quot;]Valar Morgulis
#
# The regex faithfully captured that, and the result matched nothing when
# looked up, so 28 of 1,061 curated links silently did not resolve.
_URL_JUNK = re.compile(r'(&quot;|&amp;quot;|["\'\[\]<>]|\s).*$')


def _clean_url(href: str) -> str:
    """Trim a scraped href back to the URL, dropping any BBCode wreckage."""
    href = _html.unescape(href or "").strip()
    href = _URL_JUNK.sub("", href)
    # After trimming, a bare scheme or a fragment is not a story link.
    if not href.startswith(("http://", "https://")) or len(href) < 15:
        return ""
    return href.rstrip(".,;)")


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
    DLP titles: "Title by Author - Rating" or "Title by Author [M]".
    """
    raw = _html.unescape(re.sub(r"<[^>]+>", "", raw))
    raw = re.sub(r"\s+", " ", raw).strip()

    rating = None
    m = re.search(r"\s*[\-\u2013]\s*([KTMR](?:\+|/[A-Z0-9\-]+)?|NC-?17|PG(?:-?\d+)?)\s*$", raw, re.IGNORECASE)
    if m:
        rating = m.group(1).upper(); raw = raw[:m.start()].strip()
    else:
        m2 = re.search(r"\s*\[([A-Z0-9\-/+]+)\]\s*$", raw)
        if m2:
            rating = m2.group(1).upper(); raw = raw[:m2.start()].strip()

    m = re.search(r"\s+by\s+(.+)$", raw, re.IGNORECASE)
    if m:
        return raw[:m.start()].strip(), m.group(1).strip(), rating
    return raw, "Unknown", rating


def parse_dlp_library(html_text: str) -> list[dict]:
    """Parse DLP's XenForo library list into structured entries."""
    # Find each story item's start position; slice from item N to item N+1
    starts = [m.start() for m in _ITEM_RE.finditer(html_text)]
    if not starts:
        log.warning("DLP parser: no <li class='discussionListItem'> items found")
        return []

    # Append EOF so the last slice is bounded
    starts.append(len(html_text))
    entries: list[dict] = []

    for i in range(len(starts) - 1):
        item = html_text[starts[i]:starts[i+1]]

        h = _HEADING_RE.search(item)
        if not h: continue
        thread_path = h.group(1).strip()
        if not (thread_path.startswith("threads/") or "/threads/" in thread_path):
            continue
        # Normalise to a full URL
        thread_url = thread_path if thread_path.startswith("http") else (
            f"https://forums.darklordpotter.net/{thread_path.lstrip('/')}"
        )

        title, author, rating = _parse_title_line(h.group(2))

        # Tags
        dlp_tags = []
        seen_labels = set()
        for tm in _TAG_RE.finditer(item):
            label = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(2))).strip()
            if not label: continue
            key = label.lower()
            if key in seen_labels: continue
            seen_labels.add(key)
            dlp_tags.append(label)

        # External links (FFN/AO3/etc)
        urls: dict[str, str] = {}
        for em in _EXT_RE.finditer(item):
            href = _clean_url(em.group(1))
            if not href or "darklordpotter.net" in href: continue
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


# DLP runs XenForo's thread-rating add-on, so every library thread carries a
# community star rating — but only on the THREAD page, never on the library
# list this module otherwise parses:
#
#     <div class="threadrating"> … <span class="ratings" title="4.00">
#
# That is a genuinely useful signal: DLP's list is already curated, so the
# rating separates "good enough to include" from "the best of them".
_RATING_RE = re.compile(r'<div class="threadrating".{0,400}?<span class="ratings" title="([\d.]+)"',
                        re.DOTALL)


async def fetch_dlp_rating(client, thread_url: str) -> float | None:
    """The community star rating on a DLP library thread, 0-5, or None.

    One request per thread, so callers should pace themselves — this is a
    volunteer-run forum, not an API.
    """
    try:
        r = await client.get(thread_url)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    m = _RATING_RE.search(r.text)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    # An unrated thread renders as 0.00; that is "no rating", not "rated zero".
    return value if 0 < value <= 5 else None


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
