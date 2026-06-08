"""AO3 Crawler — respects AO3's robots.txt and rate limits

AO3 does not have a public API. We crawl the search/works pages.
Rate limit: at least 1 request per 5s to avoid being banned.
AO3 blocks scrapers aggressively — always identify with User-Agent.
"""
import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from models.story import SiteEnum, RatingEnum, StatusEnum
from db.session import db_session

AO3_RATING_MAP = {
    "Not Rated": RatingEnum.not_rated,
    "General Audiences": RatingEnum.general,
    "Teen And Up Audiences": RatingEnum.teen,
    "Mature": RatingEnum.mature,
    "Explicit": RatingEnum.explicit,
}


class AO3Crawler(BaseCrawler):
    SITE = SiteEnum.ao3
    RATE_LIMIT_DELAY = 5.0   # AO3 is strict — 5s minimum
    BASE = "https://archiveofourown.org"

    async def run(self, job_type: str = "incremental") -> dict:
        stats = {"found": 0, "new": 0, "updated": 0}

        # Incremental: crawl recently updated works
        # Full: crawl from works index (very slow, millions of works)
        if job_type == "incremental":
            url = f"{self.BASE}/works?sort_column=revised_at&sort_direction=desc"
        else:
            url = f"{self.BASE}/works"

        page = 1
        max_pages = 5 if job_type == "incremental" else 500

        while page <= max_pages:
            page_url = f"{url}&page={page}"
            html = await self.fetch(page_url)
            if not html:
                break

            work_ids = self._extract_work_ids(html)
            if not work_ids:
                break

            for work_id in work_ids:
                stats["found"] += 1
                work_html = await self.fetch(f"{self.BASE}/works/{work_id}")
                if not work_html:
                    continue

                data = self._parse_work(work_id, work_html)
                if not data:
                    continue

                with db_session() as db:
                    is_new, is_updated = self.upsert_story(db, data)
                    if is_new:
                        stats["new"] += 1
                    elif is_updated:
                        stats["updated"] += 1

            page += 1

        await self.close()
        return stats

    def _extract_work_ids(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        ids = []
        for li in soup.select("li.work.blurb"):
            m = re.search(r"work_(\d+)", li.get("id", ""))
            if m:
                ids.append(m.group(1))
        return ids

    def _parse_work(self, work_id: str, html: str) -> Optional[dict]:
        soup = BeautifulSoup(html, "lxml")

        try:
            title_el = soup.select_one("h2.title.heading")
            title = title_el.get_text(strip=True) if title_el else "Untitled"

            author_el = soup.select_one("a[rel='author']")
            author = author_el.get_text(strip=True) if author_el else "Anonymous"
            author_url = (self.BASE + author_el["href"]) if author_el and author_el.get("href") else None

            summary_el = soup.select_one("div.summary blockquote")
            summary = summary_el.get_text(strip=True) if summary_el else None

            # Tags
            def get_tags(cls: str) -> list[str]:
                return [el.get_text(strip=True) for el in soup.select(f"dd.{cls} a.tag")]

            fandoms = get_tags("fandom")
            characters = get_tags("character")
            relationships = get_tags("relationship")
            additional_tags = get_tags("freeform")
            warnings = get_tags("warning")
            categories = get_tags("category")

            # Rating
            rating_el = soup.select_one("dd.rating a")
            rating_text = rating_el.get_text(strip=True) if rating_el else ""
            rating = AO3_RATING_MAP.get(rating_text, RatingEnum.not_rated)

            # Stats
            def stat(cls: str) -> int:
                el = soup.select_one(f"dd.{cls}")
                if not el:
                    return 0
                text = el.get_text(strip=True).replace(",", "")
                return int(text) if text.isdigit() else 0

            word_count = stat("words")
            kudos = stat("kudos")
            hits = stat("hits")
            bookmarks = stat("bookmarks")
            comments = stat("comments")

            # Chapters
            chapters_el = soup.select_one("dd.chapters")
            chapter_count = 1
            chapter_count_total = None
            if chapters_el:
                parts = chapters_el.get_text(strip=True).split("/")
                chapter_count = int(parts[0].replace(",", "")) if parts[0].isdigit() else 1
                if len(parts) > 1:
                    chapter_count_total = int(parts[1]) if parts[1].isdigit() else None

            # Status
            status_el = soup.select_one("dt.status")
            if status_el and "Completed" in status_el.get_text():
                status = StatusEnum.complete
            elif chapter_count_total and chapter_count >= chapter_count_total:
                status = StatusEnum.complete
            else:
                status = StatusEnum.in_progress

            # Language
            lang_el = soup.select_one("dd.language")
            language = lang_el.get_text(strip=True) if lang_el else "English"

            # Dates
            def parse_date(sel: str) -> Optional[datetime]:
                el = soup.select_one(sel)
                if not el:
                    return None
                try:
                    return datetime.strptime(el.get_text(strip=True), "%Y-%m-%d")
                except Exception:
                    return None

            published_at = parse_date("dd.published")
            updated_at = parse_date("dd.status") or parse_date("dd.published")

            is_crossover = len(fandoms) > 1

            return {
                "site_id": work_id,
                "url": f"{self.BASE}/works/{work_id}",
                "title": title,
                "author": author,
                "author_url": author_url,
                "summary": summary,
                "language": language,
                "rating": rating,
                "status": status,
                "word_count": word_count,
                "chapter_count": chapter_count,
                "chapter_count_total": chapter_count_total,
                "kudos": kudos,
                "hits": hits,
                "bookmarks": bookmarks,
                "comments": comments,
                "fandoms": fandoms,
                "characters": characters,
                "relationships": relationships,
                "tags": additional_tags,
                "warnings": warnings,
                "categories": categories,
                "ao3_archive_warnings": warnings,
                "is_crossover": is_crossover,
                "published_at": published_at,
                "updated_at": updated_at,
            }

        except Exception as e:
            print(f"[AO3] Failed to parse work {work_id}: {e}")
            return None
