"""Live AO3 search — fetches directly from AO3 search results page.
Used for hybrid mode: merges with indexed results for freshness.
"""
import asyncio
import logging
import re
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from datetime import datetime
from urllib.parse import quote, urlencode

log = logging.getLogger(__name__)

BASE = "https://archiveofourown.org"

# Patient read, quick connect: a dead host should fail fast, but a live AO3 that
# is merely grinding through a search should be waited out.
AO3_SEARCH_TIMEOUT = httpx.Timeout(connect=6.0, read=45.0, write=8.0, pool=6.0)

# AO3 asks automated clients to identify themselves. A contactable UA also means
# they can ask us to stop rather than just blocking an anonymous scraper.
HEADERS = {
    "User-Agent": "FicAtlas/1.0 (personal fanfiction index; +https://github.com/Georgexzy/ficatlas)",
}

# AO3's robots.txt disallows /works/search? outright:
#
#     Disallow: /works?        # cruel but efficient
#     Disallow: /works/search?
#
# Tag listings (/tags/<tag>/works) and individual work pages (/works/12345) are
# NOT disallowed, and both are what this module now prefers anyway — the tag
# endpoint measured 2/2 successful at 4-5s versus /works/search at 1/2 and 29s.
#
# So free-text search against AO3 is only ever run for a request a person is
# actually waiting on, never from a background loop. `automated=True` makes the
# caller skip rather than fall back to a disallowed endpoint.
ROBOTS_DISALLOWED_SEARCH = True

AO3_RATING_MAP = {
    "Not Rated": "NR",
    "General Audiences": "G",
    "Teen And Up Audiences": "T",
    "Mature": "M",
    "Explicit": "E",
}


def _build_ao3_url(params: dict) -> str:
    p = {}

    # A fandom-scoped search goes to the TAG endpoint, not /works/search.
    #
    # Measured against AO3, twice each: the tag works page succeeded 2/2 at
    # 4-5s; /works/search succeeded 1/2, taking 29s when it worked and
    # returning 525 when it didn't. /works/search is a full-text search over
    # millions of works and is the least reliable thing AO3 exposes, so it is
    # now only used when there is free text and no fandom to scope by — the one
    # case the tag endpoint cannot serve.
    fandom = (params.get("fandoms") or "").split(",")[0].strip()
    if params.get("q"):
        p["work_search[query]"] = params["q"]
    if params.get("fandoms") and not fandom:
        p["work_search[fandom_names]"] = params["fandoms"]
    if params.get("relationships"):
        p["work_search[relationship_names]"] = params["relationships"]
    if params.get("characters"):
        p["work_search[character_names]"] = params["characters"]
    if params.get("tags"):
        p["work_search[freeform_names]"] = params["tags"]

    # `.get(key, default)` does NOT apply the default when the key is present with
    # a None value, and search.py always passes status through explicitly. So this
    # raised TypeError on every single search, and search.py swallows live-fetch
    # exceptions — which is why live results silently never appeared.
    status = params.get("status") or ""
    if "complete" in status:
        p["work_search[complete]"] = "T"
    elif "in_progress" in status:
        p["work_search[complete]"] = "F"

    if params.get("word_count_min"):
        p["work_search[words_from]"] = str(params["word_count_min"])
    if params.get("word_count_max"):
        p["work_search[words_to]"] = str(params["word_count_max"])

    RATING_PARAM = {"G": "10", "T": "11", "M": "12", "E": "13", "NR": "9"}
    if params.get("ratings"):
        r = params["ratings"].split(",")[0].strip().upper()
        if r in RATING_PARAM:
            p["work_search[rating_ids][]"] = RATING_PARAM[r]

    SORT_PARAM = {
        "updated_desc": "revised_at", "published_desc": "created_at",
        "kudos_desc": "kudos_count", "hits_desc": "hits",
        "word_count_desc": "word_count", "comments_desc": "comments_count",
        "bookmarks_desc": "bookmarks_count",
    }
    sort = params.get("sort") or "relevance"
    p["work_search[sort_column]"] = SORT_PARAM.get(sort, "revised_at")
    p["work_search[sort_direction]"] = "desc"

    # The caller sets params["page"] to walk consecutive result pages, but this was
    # never put into the URL — so a multi-page fetch requested the identical page N
    # times, spent ~5s on each, and deduplicated it all back down to one page's
    # worth of results.
    page = params.get("page")
    if page and int(page) > 1:
        p["page"] = str(int(page))

    # Must be percent-encoded: values carry spaces ("harry potter"), slashes
    # ("Draco Malfoy/Hermione Granger") and ampersands, all of which would
    # otherwise produce a malformed URL or silently truncate the query.
    qs = urlencode(p)
    if fandom:
        # AO3 escapes the punctuation in canonical tag names: "." -> "*d*",
        # "/" -> "*s*", "&" -> "*a*". Without this, "Harry Potter - J. K.
        # Rowling" 404s.
        tag = (fandom.replace("*", "*a*").replace(".", "*d*")
                     .replace("/", "*s*").replace("&", "*a*"))
        return f"{BASE}/tags/{quote(tag)}/works?{qs}"

    # No fandom to scope by: /works/search is the only endpoint that can do a
    # free-text query. Note it is /works/search and NOT /works — the latter is
    # the tag listing, which accepts work_search params but ignores &page
    # entirely (verified: 20/20 overlap between "pages" 1-3).
    return f"{BASE}/works/search?{qs}"


def _parse_blurb(item) -> Optional[dict]:
    try:
        m = re.search(r"work_(\d+)", item.get("id", ""))
        if not m:
            return None
        work_id = m.group(1)

        title_el = item.select_one("h4.heading a:first-child")
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        author_el = item.select_one("a[rel='author']")
        author = author_el.get_text(strip=True) if author_el else "Anonymous"
        author_url = (BASE + author_el["href"]) if author_el and author_el.get("href") else None

        summary_el = item.select_one("blockquote.summary")
        summary = summary_el.get_text(strip=True) if summary_el else None

        def get_tags(cls):
            return [el.get_text(strip=True) for el in item.select(f"li.{cls} a")]

        fandoms       = [el.get_text(strip=True) for el in item.select("h5.fandoms a")]
        relationships = get_tags("relationships")
        characters    = get_tags("characters")
        tags          = get_tags("freeforms")
        warnings      = get_tags("warnings")
        categories    = get_tags("category")

        rating_el = item.select_one("span.rating")
        rating = AO3_RATING_MAP.get(rating_el.get("title", "") if rating_el else "", "NR")

        def stat(cls):
            el = item.select_one(f"dd.{cls}")
            if not el:
                return 0
            # AO3 stats can contain nbsp, commas, and trailing labels. Pull digits only.
            import re as _re
            txt = el.get_text(strip=True).replace("\xa0", " ")
            digits = _re.sub(r"[^\d]", "", txt)
            return int(digits) if digits else 0

        status_el = item.select_one("dt.status")
        status = "complete" if status_el and "Completed" in status_el.get_text() else "in_progress"

        lang_el = item.select_one("dd.language")
        language = lang_el.get_text(strip=True) if lang_el else "English"

        updated_el = item.select_one("p.datetime")
        updated_at = None
        if updated_el:
            try:
                updated_at = datetime.strptime(updated_el.get_text(strip=True), "%d %b %Y").isoformat()
            except Exception:
                pass

        return {
            "id": f"live_ao3_{work_id}",
            "site": "ao3",
            "url": f"{BASE}/works/{work_id}",
            "title": title,
            "author": author,
            "author_url": author_url,
            "summary": summary,
            "language": language,
            "rating": rating,
            "status": status,
            "word_count": stat("words"),
            "chapter_count": stat("chapters") or 1,
            "chapter_count_total": None,
            "kudos": stat("kudos"),
            "hits": stat("hits"),
            "bookmarks": stat("bookmarks"),
            "comments": stat("comments"),
            "fandoms": fandoms,
            "relationships": relationships,
            "characters": characters,
            "tags": tags,
            "warnings": warnings,
            "categories": categories,
            "genres": [],
            "published_at": None,
            "updated_at": updated_at,
            "is_live": True,
        }
    except Exception:
        return None


async def _fetch_page(client: httpx.AsyncClient, params: dict, page_num: int) -> list[dict]:
    """One result page -> parsed blurbs. Never raises; returns [] on any failure."""
    page_params = dict(params)
    page_params["page"] = page_num
    url = _build_ao3_url(page_params)
    try:
        resp = await client.get(url)
    except Exception as e:
        # This used to be silent, which is how a TypeError in the URL builder went
        # unnoticed long enough for live fetch to be dead on every search while
        # still appearing to "work".
        log.warning("AO3 live page %s failed: %s: %s", page_num, type(e).__name__, e)
        return []
    if resp.status_code != 200:
        # 525 is a Cloudflare-to-origin failure on AO3's side and is transient.
        log.warning("AO3 live page %s returned HTTP %s", page_num, resp.status_code)
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    out = []
    for item in soup.select("li.work.blurb"):
        d = _parse_blurb(item)
        if d:
            out.append(d)
    if not out:
        log.info("AO3 live page %s had no work blurbs", page_num)
    return out


async def fetch_live_ao3(params: dict, limit: int = 20, pages: int = 1,
                         automated: bool = False) -> list[dict]:
    """Fetch live AO3 results, merging `pages` consecutive result pages.

    Pages are fetched CONCURRENTLY. They were fetched one after another, and since
    AO3's /works/search is a full-text search over millions of works taking 18-21s
    per page, three pages cost ~31s serially versus roughly the slowest single page
    in parallel. Same number of requests to AO3, just overlapped.
    """
    # A background loop must not touch /works/search — see ROBOTS_DISALLOWED_SEARCH.
    # Without a fandom there is no allowed endpoint, so it simply does nothing.
    # AO3 is in a cooldown after repeated throttling. A background loop can sleep
    # it out; a reader's search cannot, and the live top-up runs on that path —
    # so skip AO3 for this search rather than holding the response open for up to
    # fifteen minutes. The index still answers; it just does not get topped up.
    import ao3_budget
    left = ao3_budget.paused_for()
    if left > 0:
        log.info(f"skipping live AO3 fetch: cooling down for another {left:.0f}s")
        return []

    if automated and not (params.get("fandoms") or "").strip():
        log.info("skipping automated AO3 free-text fetch: /works/search is robots-disallowed")
        return []

    async with httpx.AsyncClient(headers=HEADERS, timeout=AO3_SEARCH_TIMEOUT,
                                 follow_redirects=True) as client:
        batches = await asyncio.gather(
            *(_fetch_page(client, params, n) for n in range(1, pages + 1)),
            return_exceptions=True,
        )

    all_results: list[dict] = []
    seen_ids: set[str] = set()
    for batch in batches:
        if isinstance(batch, BaseException):
            log.warning("AO3 live page raised: %s", batch)
            continue
        for d in batch:
            if d["id"] in seen_ids:
                continue
            seen_ids.add(d["id"])
            all_results.append(d)
            if len(all_results) >= limit:
                return all_results
    return all_results
