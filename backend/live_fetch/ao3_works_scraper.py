"""
AO3 tag-works filtered scraper.

Where the Atom feed gives only 25 newest works per tag with no filters, AO3's
canonical /tags/{TAG}/works listing page supports rich filtering via URL params
and pagination. We hit that page, apply filters, and scrape work blurbs from
the HTML. Each blurb has all the metadata we need (title, author, summary,
word count, rating, fandoms, characters, ships, tags).

Filter params (work_search[...] in the URL):
  - words_from / words_to     — word count range
  - complete                  — "T" to require complete only
  - sort_column               — revised_at | created_at | kudos_count | word_count | hits
  - sort_direction            — desc | asc
  - excluded_tag_names        — exclude tags (comma-separated)
  - language_id               — language ID (English=1)
  - rating_ids                — rating IDs (G=10, T=11, M=12, E=13, NR=9)
  - complete                  — T/F

URL: /tags/{escaped}/works?work_search[words_from]=100000&page=1

20 works per page. AO3 throttles >1 req/sec — we use 3s delays.
"""
import re
import html as _html
import asyncio
import logging
from datetime import datetime
import httpx

from .ao3_feeds import ao3_escape, HEADERS, _get_with_fallback, PRIMARY, MIRROR, AO3_TIMEOUT

log = logging.getLogger(__name__)

REQUEST_DELAY = 3.5  # AO3 etiquette: >1s between requests

RATING_IDS = {
    "G":  "10",
    "T":  "11",
    "M":  "12",
    "E":  "13",
    "NR": "9",
}

# Reverse for parsing
_RATING_NAMES = {
    "General Audiences":     "G",
    "Teen And Up Audiences": "T",
    "Mature":                "M",
    "Explicit":              "E",
    "Not Rated":             "NR",
}


def build_works_url(
    tag: str,
    min_words: int | None = None,
    max_words: int | None = None,
    complete_only: bool = False,
    sort: str = "revised_at",          # revised_at | created_at | kudos_count | word_count | hits
    direction: str = "desc",
    excluded_tags: list[str] | None = None,
    ratings: list[str] | None = None,
    language_id: str | None = None,
    page: int = 1,
    collection: str | None = None,     # e.g. "hpfanfiction_hpff" for HPFFA Open Doors import
) -> str:
    """Build a tag-works URL with the requested filter params.
    If `collection` is set, scopes to /collections/{slug}/works (Open Doors imports
    like HPFFA, fanlib.net, etc. live here) instead of /tags/{tag}/works.
    `tag` is still applied as a fandom filter inside the collection.
    """
    escaped = ao3_escape(tag) if tag else None
    params: list[str] = []

    def add(k: str, v):
        params.append(f"{k}={v}")

    if min_words is not None: add("work_search%5Bwords_from%5D", min_words)
    if max_words is not None: add("work_search%5Bwords_to%5D",   max_words)
    if complete_only:         add("work_search%5Bcomplete%5D",   "T")

    add("work_search%5Bsort_column%5D",    sort)
    add("work_search%5Bsort_direction%5D", direction)

    if excluded_tags:
        from urllib.parse import quote
        joined = ",".join(excluded_tags)
        add("work_search%5Bexcluded_tag_names%5D", quote(joined))

    if ratings:
        for r in ratings:
            rid = RATING_IDS.get(r.upper())
            if rid:
                add("work_search%5Brating_ids%5D%5B%5D", rid)

    if language_id:
        add("work_search%5Blanguage_id%5D", language_id)

    if page > 1:
        add("page", page)

    if collection:
        # Inside a collection, optionally narrow by fandom via the query field
        if escaped:
            from urllib.parse import quote
            add("work_search%5Bfandom_names%5D", quote(tag))
        return f"/collections/{collection}/works?" + "&".join(params)

    return f"/tags/{escaped}/works?" + "&".join(params)


def _parse_work_blurb(blurb_html: str, host: str = "https://archiveofourown.org") -> dict | None:
    """Parse a single work <li class='work blurb group'>...</li> into our story dict."""
    # Work ID
    id_m = re.search(r'<li[^>]+id="work_(\d+)"', blurb_html)
    if not id_m: return None
    work_id = id_m.group(1)

    # Title + author
    # The work link is NOT always /works/<id>. Inside a collection listing —
    # which is exactly how HPFFA, HexFiles and the other Open Doors imports are
    # scraped — Otwarchive emits /collections/<name>/works/<id>. Requiring the
    # bare form meant every work in every collection parsed as "Untitled" by
    # "Anonymous" with no metadata at all, which is why the HPFFA rows already
    # in the index are 100% empty.
    h_m = re.search(
        r'<h4 class="heading">\s*<a href="(?:/collections/[^/"]+)?/works/\d+[^"]*">(.*?)</a>'
        r'\s*(?:by\s*(?:<!--.*?-->)?\s*<a [^>]*rel="author"[^>]*>(.*?)</a>)?',
        blurb_html, re.DOTALL,
    )
    title  = _html.unescape(re.sub(r"<[^>]+>", "", h_m.group(1))).strip() if h_m else "Untitled"
    author = _html.unescape(re.sub(r"<[^>]+>", "", h_m.group(2) or "")).strip() if h_m and h_m.group(2) else "Anonymous"

    # Fandoms — <h5 class="fandoms"><a class="tag">…</a></h5>
    fandoms = []
    fm = re.search(r'<h5 class="fandoms[^"]*">(.*?)</h5>', blurb_html, re.DOTALL)
    if fm:
        for tm in re.finditer(r'<a[^>]+class="tag"[^>]*>(.*?)</a>', fm.group(1), re.DOTALL):
            fandoms.append(_html.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip())

    # Required tags: rating, warnings, category, status
    rating = "NR"
    rm = re.search(r'<span class="rating[^"]*"[^>]+title="([^"]+)"', blurb_html)
    if rm:
        rating = _RATING_NAMES.get(rm.group(1), "NR")

    warnings = []
    for wm in re.finditer(r'<span class="warnings[^"]*"[^>]+title="([^"]+)"', blurb_html):
        # AO3 sometimes lists multiple via "comma, separated" inside title
        warnings.extend([w.strip() for w in wm.group(1).split(",") if w.strip()])

    categories = []
    for cm in re.finditer(r'<span class="category[^"]*"[^>]+title="([^"]+)"', blurb_html):
        categories.extend([c.strip() for c in cm.group(1).split(",") if c.strip()])

    status = "in_progress"
    sm = re.search(r'<span class="iswip[^"]*"[^>]+title="([^"]+)"', blurb_html)
    if sm and "Complete" in sm.group(1):
        status = "complete"

    # Tags section: relationships / characters / freeforms
    def collect_class(cls: str) -> list[str]:
        out = []
        # Otwarchive emits class='characters' with SINGLE quotes here, and
        # wraps warning tags in <strong>. The double-quoted, unwrapped
        # pattern this used to require matched nothing at all.
        pattern = (
            r'<li[^>]*class=[\"\']' + cls + r'[\"\'][^>]*>\s*(?:<strong>\s*)?'
            r'<a[^>]+class=\"tag\"[^>]*>(.*?)</a>'
        )
        for m in re.finditer(pattern, blurb_html, re.DOTALL):
            out.append(_html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip())
        return out

    relationships = collect_class("relationships")
    characters    = collect_class("characters")
    freeforms     = collect_class("freeforms")

    # Stats — language, words, chapters, kudos, hits, bookmarks, comments
    word_count = 0
    wm = re.search(r'<dd class="words">([\d,]+)</dd>', blurb_html)
    if wm: word_count = int(wm.group(1).replace(",", ""))

    chapter_count = 1
    chapter_count_total = None
    cm = re.search(r'<dd class="chapters">[^<]*(?:<a[^>]*>)?(\d+)(?:</a>)?/(\d+|\?)', blurb_html)
    if cm:
        chapter_count = int(cm.group(1))
        if cm.group(2).isdigit():
            chapter_count_total = int(cm.group(2))

    def stat_int(name: str) -> int:
        m = re.search(rf'<dd class="{name}">\s*(?:<a[^>]*>)?\s*([\d,]+)', blurb_html)
        return int(m.group(1).replace(",", "")) if m else 0

    kudos     = stat_int("kudos")
    hits      = stat_int("hits")
    bookmarks = stat_int("bookmarks")
    comments  = stat_int("comments")

    language = "English"
    lm = re.search(r'<dd class="language"[^>]*>([^<]+)</dd>', blurb_html)
    if lm: language = lm.group(1).strip()

    # Summary blockquote
    summary = None
    smq = re.search(r'<blockquote class="userstuff summary">(.*?)</blockquote>', blurb_html, re.DOTALL)
    if smq:
        text = re.sub(r"<[^>]+>", " ", smq.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        summary = _html.unescape(text)[:2000] or None

    # Updated date — usually in <p class="datetime"> as "15 Mar 2024"
    updated_at = None
    dm = re.search(r'<p class="datetime">([^<]+)</p>', blurb_html)
    if dm:
        try:
            updated_at = datetime.strptime(dm.group(1).strip(), "%d %b %Y").isoformat()
        except Exception:
            pass

    return {
        "id":           f"live_ao3_{work_id}",
        "site_id":      work_id,
        "url":          f"{host}/works/{work_id}",
        "title":        title,
        "author":       author,
        "summary":      summary,
        "updated_at":   updated_at,
        "fandoms":      fandoms,
        "characters":   characters,
        "relationships": relationships,
        "tags":         relationships + characters + freeforms,
        "rating":       rating,
        "status":       status,
        "language":     language,
        "word_count":   word_count,
        "chapter_count": chapter_count,
        "chapter_count_total": chapter_count_total,
        "kudos":        kudos,
        "hits":         hits,
        "bookmarks":    bookmarks,
        "comments":     comments,
        "warnings":     warnings,
        "categories":   categories,
    }


def parse_works_page(html_text: str, host: str = "https://archiveofourown.org",
                     page: int | None = None) -> tuple[list[dict], bool]:
    """Parse a tag-works HTML page. Returns (entries, has_next_page)."""
    # Slice between work <li> starts rather than matching to the first </li>.
    #
    # A work blurb CONTAINS nested <li>s — the rating/warning/category/status
    # icons live in <ul class="required-tags">. A non-greedy ".*?</li>" therefore
    # ended each blurb a few hundred bytes in, right after the first icon, and
    # threw away the tags, summary and the entire <dl class="stats"> block.
    # Everything after that point parsed as empty, which is why imported
    # collection works had no characters, ships, word counts, dates or summaries.
    starts = [m.start() for m in re.finditer(
        r'<li[^>]*id="work_\d+"[^>]*class="[^"]*work blurb', html_text)]
    bounds = starts + [len(html_text)]
    blurbs = [html_text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]
    entries = [e for e in (_parse_work_blurb(b, host) for b in blurbs) if e]

    # "next page" link presence.
    #
    # Otwarchive does NOT emit rel="next" — verified against a live listing that
    # has 42 pages. The old check therefore returned False on page 1 every time,
    # so every scrape stopped after 20 works no matter what max_pages said. That
    # is the real reason these archives never grew: the Library button's
    # "20 pages" was never more than one.
    #
    # The marker is <li class="next"> holding a link; on the last page the same
    # <li> holds a disabled <span> instead, so requiring the <a> is what makes
    # it terminate. The page+1 link is a second, independent signal in case the
    # markup shifts again.
    has_next = bool(re.search(r'<li class="next">\s*<a\b', html_text))
    if not has_next and page:
        has_next = bool(re.search(rf'page={page + 1}(?![0-9])', html_text))

    return entries, has_next


async def scrape_tag_works(
    tag: str,
    min_words: int | None = None,
    max_words: int | None = None,
    complete_only: bool = False,
    sort: str = "revised_at",
    direction: str = "desc",
    excluded_tags: list[str] | None = None,
    ratings: list[str] | None = None,
    max_pages: int = 5,
    start_page: int = 1,
    collection: str | None = None,
    base_url: str | None = None,        # non-AO3 Otwarchive host, e.g. https://squidgeworld.org
    on_progress: callable = None,    # optional: invoked with a dict {page, pages_ok, pages_failed, found}
) -> dict:
    """
    Scrape filtered works from a tag (or an AO3 collection) with pagination.
    Returns a diagnostic dict so callers can distinguish "fetched but no matches"
    from "all fetches failed":
        {
            "entries":      list of work dicts,
            "pages_ok":     int — how many pages came back 200,
            "pages_failed": int — how many pages failed (rate limit, 5xx, network),
            "tried_url":    str — first URL we hit (for debugging),
        }
    If `on_progress` is supplied, it's called after each page attempt with a
    snapshot of progress so a background runner can stream updates to the UI.

    `start_page` lets a caller resume where a previous run stopped. Without it
    every run began at page 1, which is fine for a 5-page interactive job but
    makes a full archive import impossible: re-reading the first N pages every
    time means the tail is never reached. The returned "next_page"/"exhausted"
    let the caller persist a cursor.
    """
    all_entries: list[dict] = []
    seen_ids: set[str] = set()
    pages_ok = 0
    pages_failed = 0
    first_url = ""

    async with httpx.AsyncClient(
        headers=HEADERS, timeout=AO3_TIMEOUT, follow_redirects=True,
    ) as client:
        last_page = start_page - 1
        exhausted = False
        for page in range(start_page, start_page + max_pages):
            path = build_works_url(
                tag, min_words=min_words, max_words=max_words,
                complete_only=complete_only, sort=sort, direction=direction,
                excluded_tags=excluded_tags, ratings=ratings, page=page,
                collection=collection,
            )
            if page == 1: first_url = path
            log.info(f"AO3 scrape page {page}: {path[:120]}")
            if on_progress:
                on_progress({"page": page, "pages_ok": pages_ok, "pages_failed": pages_failed,
                             "found": len(all_entries), "msg": f"Fetching page {page} of {max_pages}…"})
            r = await _get_with_fallback(client, path, bases=(base_url,) if base_url else None)
            if not r:
                pages_failed += 1
                log.warning(f"Page {page} failed, stopping")
                if on_progress:
                    on_progress({"page": page, "pages_ok": pages_ok, "pages_failed": pages_failed,
                                 "found": len(all_entries), "msg": f"Page {page} failed, stopping"})
                break

            pages_ok += 1
            entries, has_next = parse_works_page(
                r.text, host=base_url or "https://archiveofourown.org", page=page)
            new = [e for e in entries if e["site_id"] not in seen_ids]
            for e in new: seen_ids.add(e["site_id"])
            all_entries.extend(new)
            log.info(f"  page {page}: {len(entries)} works ({len(new)} new), has_next={has_next}")
            if on_progress:
                on_progress({"page": page, "pages_ok": pages_ok, "pages_failed": pages_failed,
                             "found": len(all_entries),
                             "msg": f"Page {page}: {len(entries)} works ({len(all_entries)} total)"})

            last_page = page
            if not has_next or len(entries) == 0:
                exhausted = True
                break
            if page < max_pages:
                await asyncio.sleep(REQUEST_DELAY)

    return {
        "entries":      all_entries,
        "pages_ok":     pages_ok,
        "pages_failed": pages_failed,
        "tried_url":    first_url,
        # Where a resumable caller should continue, and whether there is any
        # point. `exhausted` means AO3 stopped offering a "next" link, i.e. the
        # archive has been walked end to end.
        "next_page":    last_page + 1,
        "exhausted":    exhausted,
    }
