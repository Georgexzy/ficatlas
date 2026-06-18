"""Live AO3 search — fetches directly from AO3 search results page.
Used for hybrid mode: merges with indexed results for freshness.
"""
import re
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from datetime import datetime

BASE = "https://archiveofourown.org"
HEADERS = {
    "User-Agent": "FicAtlas/0.1 (fanfiction discovery; contact: admin@ficatlas.app)",
}

AO3_RATING_MAP = {
    "Not Rated": "NR",
    "General Audiences": "G",
    "Teen And Up Audiences": "T",
    "Mature": "M",
    "Explicit": "E",
}


def _build_ao3_url(params: dict) -> str:
    p = {}

    if params.get("q"):
        p["work_search[query]"] = params["q"]
    if params.get("fandoms"):
        p["work_search[fandom_names]"] = params["fandoms"]
    if params.get("relationships"):
        p["work_search[relationship_names]"] = params["relationships"]
    if params.get("characters"):
        p["work_search[character_names]"] = params["characters"]
    if params.get("tags"):
        p["work_search[freeform_names]"] = params["tags"]

    status = params.get("status", "")
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
    sort = params.get("sort", "relevance")
    p["work_search[sort_column]"] = SORT_PARAM.get(sort, "revised_at")
    p["work_search[sort_direction]"] = "desc"

    qs = "&".join(f"{k}={v}" for k, v in p.items())
    return f"{BASE}/works?{qs}"


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


async def fetch_live_ao3(params: dict, limit: int = 20, pages: int = 1) -> list[dict]:
    """Fetch live AO3 results. `pages` fetches N consecutive result pages and merges them."""
    all_results: list[dict] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for page_num in range(1, pages + 1):
            page_params = dict(params)
            page_params["page"] = page_num
            url = _build_ao3_url(page_params)
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    break
                soup = BeautifulSoup(resp.text, "lxml")
                items = soup.select("li.work.blurb")
                if not items:
                    break
                for item in items:
                    d = _parse_blurb(item)
                    if d and d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        all_results.append(d)
                        if len(all_results) >= limit:
                            return all_results
            except Exception:
                break

    return all_results
