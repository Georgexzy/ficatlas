"""Direct AO3 import fallback, for when FicHub throttles the shared IP.

FicHub throttles per-IP, and once it decides the IP is over its (unpublished)
limit it slow-rolls every request past the ~30s Next.js proxy timeout — an
import then fails without ever reaching the EPUB. AO3 works are the most common
import target, and AO3 itself lets us fetch the work directly (it is the
archive the work lives on, so there is no licensing or republish concern beyond
what FicHub already imposes). When the FicHub path is throttled, an AO3 URL can
be fetched straight from archiveofourown.org instead: all chapters come back on
one page via `view_full_work`, and the parser below turns it into the same shape
`parse_epub` produces, so the rest of `import_url` is unchanged.

The request honours the process-wide AO3 budget (shared with the title-repair
worker) so this fallback never hammers the archive that the rest of the backend
is already politely queuing against.

Deliberately AO3-only. FF.net has no equivalent direct route (Cloudflare), so
those imports stay on FicHub and get a clear throttle error instead.
"""

import logging
import re

import httpx
from bs4 import BeautifulSoup

import ao3_budget
from ao3_title_repair import UA

log = logging.getLogger(__name__)


def ao3_work_id(url: str) -> str | None:
    m = re.search(r"archiveofourown\.org/works/(\d+)", url)
    return m.group(1) if m else None


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el is not None else ""


def _chapter_content(us) -> str:
    """Chapter body as readable text with paragraph breaks preserved."""
    for s in us.find_all(["script", "style"]):
        s.decompose()
    lines = [p.get_text("\n", strip=True) for p in us.find_all(["p", "h1", "h2", "h3", "h4", "blockquote"])]
    lines = [l for l in lines if l]
    return "\n\n".join(lines)


def _extract_chapters(soup) -> list[dict]:
    chapters: list[dict] = []
    for div in soup.select("div.chapter[id^='chapter-']"):
        num_m = re.search(r"\d+$", div.get("id", "") or "")
        number = int(num_m.group(0)) if num_m else (len(chapters) + 1)
        title = _text(div.find("h3", class_="title"))
        # The chapter body is a div.userstuff inside the chapter. The work
        # summary also uses userstuff but lives in a blockquote.summary before
        # the chapters, so prefer a userstuff whose nearest parent is this
        # chapter and not a summary block.
        content_el = None
        for us in div.select("div.userstuff"):
            if us.find_parent("blockquote", class_="summary") is None:
                content_el = us
                break
        if content_el is None:
            continue
        content = _chapter_content(content_el)
        if len(content.split()) < 5:
            continue
        chapters.append({
            "number": number,
            "title": title or None,
            "content": content,
            "word_count": len(content.split()),
        })
    return chapters


def fetch_ao3_full_work(url: str) -> dict | None:
    """Fetch an AO3 work directly (all chapters in one page) as an import dict.

    Returns None when the work cannot be reached or carries no parseable
    chapters — callers should then report a clear error rather than retry.
    """
    work_id = ao3_work_id(url)
    if not work_id:
        return None

    ao3_budget.wait()
    with httpx.Client(headers=UA, follow_redirects=True, timeout=60.0) as client:
        try:
            r = client.get(
                f"https://archiveofourown.org/works/{work_id}?view_full_work=true&view_adult=true",
            )
        except Exception as e:
            log.warning("ao3 import: fetch failed: %s: %s", type(e).__name__, e)
            return None
        ao3_budget.note_response(r.status_code, r.headers.get("retry-after"))
        if r.status_code != 200:
            log.warning("ao3 import: status %s for work %s", r.status_code, work_id)
            return None

    soup = BeautifulSoup(r.text, "lxml")
    title = _text(soup.select_one("h2.title.heading"))
    chapters = _extract_chapters(soup)
    if not title or not chapters:
        return None

    author = _text(soup.find("a", rel="author")) or None
    summary_el = soup.select_one("blockquote.summary div.userstuff")
    language = _text(soup.select_one("dd.language")) or None

    return {
        "title": title,
        "author": author or "Unknown",
        "summary": _text(summary_el) or None,
        "language": language,
        "word_count": sum(c["word_count"] for c in chapters),
        "chapters": chapters,
    }
