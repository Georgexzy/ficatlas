"""FanFiction.net Crawler

FF.net has even less metadata than AO3 — no tags, no relationships field,
only genres, characters (limited), category, and basic stats.
We map what's available and clearly mark gaps.
"""
import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from models.story import SiteEnum, RatingEnum, StatusEnum
from db.session import db_session

FFNET_RATING_MAP = {
    "K":  RatingEnum.general,
    "K+": RatingEnum.general,
    "T":  RatingEnum.teen,
    "M":  RatingEnum.mature,
}


class FFNetCrawler(BaseCrawler):
    SITE = SiteEnum.ffnet
    RATE_LIMIT_DELAY = 3.0
    BASE = "https://www.fanfiction.net"

    async def run(self, job_type: str = "incremental") -> dict:
        stats = {"found": 0, "new": 0, "updated": 0}

        # FF.net browse URL sorted by update time
        # Category: Harry Potter = /book/Harry-Potter/
        # For MVP we crawl the general updated list
        browse_urls = [
            f"{self.BASE}/book/Harry-Potter/?srt=1",   # updated
        ]

        for browse_url in browse_urls:
            page = 1
            max_pages = 5 if job_type == "incremental" else 100

            while page <= max_pages:
                url = browse_url + f"&p={page}"
                html = await self.fetch(url)
                if not html:
                    break

                stories = self._parse_listing_page(html)
                if not stories:
                    break

                for data in stories:
                    stats["found"] += 1
                    with db_session() as db:
                        is_new, is_updated = self.upsert_story(db, data)
                        if is_new:
                            stats["new"] += 1
                        elif is_updated:
                            stats["updated"] += 1

                page += 1

        await self.close()
        return stats

    def _parse_listing_page(self, html: str) -> list[dict]:
        """FF.net listing pages contain full story metadata in the blurb."""
        soup = BeautifulSoup(html, "lxml")
        results = []

        for item in soup.select("div.z-list.zhover"):
            try:
                data = self._parse_story_item(item)
                if data:
                    results.append(data)
            except Exception as e:
                print(f"[FFNet] Failed to parse item: {e}")

        return results

    def _parse_story_item(self, item) -> Optional[dict]:
        # Title + URL
        title_el = item.select_one("a.stitle")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        m = re.search(r"/s/(\d+)/", href)
        if not m:
            return None
        story_id = m.group(1)
        url = f"{self.BASE}/s/{story_id}/1/"

        # Author
        author_el = item.select_one("a[href*='/u/']")
        author = author_el.get_text(strip=True) if author_el else "Anonymous"
        author_url = (self.BASE + author_el["href"]) if author_el and author_el.get("href") else None

        # Summary
        summary_el = item.select_one("div.z-indent")
        summary = summary_el.get_text(strip=True) if summary_el else None

        # Metadata strip: "Rated: T - English - Romance/Drama - Chapters: 12 - Words: 45,219 - ..."
        xgray = item.select_one("div.z-padtop2.xgray")
        meta_text = xgray.get_text(" ", strip=True) if xgray else ""

        rating = self._extract_rated(meta_text)
        language = self._extract_language(meta_text)
        genres = self._extract_genres(meta_text)
        word_count = self._extract_words(meta_text)
        chapter_count = self._extract_chapters(meta_text)
        status = self._extract_status(meta_text, chapter_count)
        updated_at, published_at = self._extract_dates(xgray)
        characters = self._extract_characters(meta_text)
        fandoms = self._extract_fandom(item)

        return {
            "site_id": story_id,
            "url": url,
            "title": title,
            "author": author,
            "author_url": author_url,
            "summary": summary,
            "language": language or "English",
            "rating": rating,
            "status": status,
            "word_count": word_count,
            "chapter_count": chapter_count,
            "chapter_count_total": chapter_count if status == StatusEnum.complete else None,
            "fandoms": fandoms,
            "characters": characters,
            "genres": genres,
            "tags": [],
            "relationships": [],
            "warnings": [],
            "categories": [],
            "kudos": 0,
            "hits": self._extract_stat(meta_text, "Favs"),
            "bookmarks": 0,
            "comments": self._extract_stat(meta_text, "Reviews"),
            "favourites": self._extract_stat(meta_text, "Favs"),
            "ffnet_category": None,
            "is_crossover": "Crossover" in (item.get_text() or ""),
            "updated_at": updated_at,
            "published_at": published_at,
        }

    # ── Parsing helpers ───────────────────────────────────────────────────

    def _extract_rated(self, text: str) -> RatingEnum:
        m = re.search(r"Rated:\s*Fiction\s*([KTM]\+?)", text)
        if m:
            return FFNET_RATING_MAP.get(m.group(1), RatingEnum.not_rated)
        return RatingEnum.not_rated

    def _extract_language(self, text: str) -> Optional[str]:
        # After "Rated: T - " comes the language
        m = re.search(r"Rated:\s*Fiction\s*\S+\s*-\s*([A-Za-zÀ-ÿ]+)", text)
        return m.group(1) if m else None

    def _extract_genres(self, text: str) -> list[str]:
        m = re.search(r"(?:English|French|Spanish|German|Japanese)\s*-\s*([A-Za-z/]+)\s*-\s*(?:Char|Chapter|Words)", text)
        if m:
            return [g.strip() for g in m.group(1).split("/")]
        return []

    def _extract_words(self, text: str) -> int:
        m = re.search(r"Words:\s*([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else 0

    def _extract_chapters(self, text: str) -> int:
        m = re.search(r"Chapters:\s*(\d+)", text)
        return int(m.group(1)) if m else 1

    def _extract_status(self, text: str, chapters: int) -> StatusEnum:
        if "Status: Complete" in text:
            return StatusEnum.complete
        return StatusEnum.in_progress

    def _extract_stat(self, text: str, key: str) -> int:
        m = re.search(rf"{key}:\s*([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else 0

    def _extract_dates(self, xgray) -> tuple[Optional[datetime], Optional[datetime]]:
        updated = None
        published = None
        if not xgray:
            return updated, published
        for span in xgray.find_all("span", attrs={"data-xutime": True}):
            ts = int(span["data-xutime"])
            dt = datetime.utcfromtimestamp(ts)
            title = span.get("title", "")
            if "Updated" in title or updated is None:
                updated = dt
            else:
                published = dt
        return updated, published

    def _extract_characters(self, text: str) -> list[str]:
        m = re.search(r"Characters?:\s*([^\-]+)", text)
        if m:
            raw = m.group(1).strip()
            return [c.strip() for c in re.split(r",|\[|\]", raw) if c.strip()]
        return []

    def _extract_fandom(self, item) -> list[str]:
        # Try to get fandom from breadcrumb or title attribute
        fandom_el = item.find_previous("div", class_="lc-left")
        if fandom_el:
            return [fandom_el.get_text(strip=True)]
        return []
