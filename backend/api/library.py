"""Library API — downloads, EPUB uploads, hosted stories."""
import os, re, uuid, zipfile, io, logging
import httpx
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from sqlalchemy.orm import Session
from datetime import datetime

from db.session import get_db
from models.story import Story, Chapter, SiteEnum, RatingEnum, StatusEnum

log = logging.getLogger(__name__)
router = APIRouter()

# ── FicHub-powered downloads (works around Cloudflare on AO3/FFnet) ──────────

FICHUB_API = "https://fichub.net/api/v0"


async def fetch_from_fichub(url: str) -> dict:
    """Get metadata + epub URL from FicHub."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
        r = await c.get(f"{FICHUB_API}/epub", params={"q": url},
                        headers={"User-Agent": "FicAtlas/1.0"})
        if r.status_code != 200:
            raise HTTPException(502, f"FicHub returned {r.status_code}")
        return r.json()


async def fetch_epub_bytes(epub_url: str) -> bytes:
    """Fetch the actual epub binary from FicHub's URL."""
    full_url = epub_url if epub_url.startswith("http") else f"https://fichub.net{epub_url}"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as c:
        r = await c.get(full_url, headers={"User-Agent": "FicAtlas/1.0"})
        if r.status_code != 200:
            raise HTTPException(502, f"EPUB fetch returned {r.status_code}")
        return r.content


# ── EPUB parsing (lightweight, no external lib) ──────────────────────────────

def parse_epub(data: bytes) -> dict:
    """Extract metadata + chapter text from an EPUB."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()

    # Find OPF (metadata) file
    opf_path = None
    for n in names:
        if n.endswith(".opf"):
            opf_path = n
            break
    if not opf_path:
        raise HTTPException(400, "Invalid EPUB — no OPF file")

    opf = z.read(opf_path).decode("utf-8", errors="ignore")

    def find(pattern, text):
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else None

    title  = find(r"<dc:title[^>]*>([^<]+)</dc:title>", opf) or "Untitled"
    author = find(r"<dc:creator[^>]*>([^<]+)</dc:creator>", opf) or "Unknown"
    lang   = find(r"<dc:language[^>]*>([^<]+)</dc:language>", opf) or "English"
    desc   = find(r"<dc:description[^>]*>([^<]+)</dc:description>", opf)

    # Find chapter HTML files in reading order from <spine>
    spine_ids = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
    items = {}
    for m in re.finditer(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf):
        items[m.group(1)] = m.group(2)

    base_dir = os.path.dirname(opf_path)
    chapters = []
    for idx, sid in enumerate(spine_ids):
        href = items.get(sid)
        if not href: continue
        full_path = os.path.join(base_dir, href) if base_dir else href
        full_path = full_path.replace("\\", "/").replace("./", "")
        try:
            html = z.read(full_path).decode("utf-8", errors="ignore")
        except KeyError:
            # Try variations
            for n in names:
                if n.endswith(href):
                    html = z.read(n).decode("utf-8", errors="ignore")
                    break
            else:
                continue

        # Extract body content
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        body = body_match.group(1) if body_match else html

        # Pull chapter title
        title_match = re.search(r"<h\d[^>]*>([^<]+)</h\d>", body)
        ch_title = title_match.group(1).strip() if title_match else None

        # Strip scripts/styles/css
        body = re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL)

        # Word count
        text_only = re.sub(r"<[^>]+>", " ", body)
        words = len(text_only.split())

        chapters.append({
            "number": idx + 1, "title": ch_title, "content": body, "word_count": words,
        })

    total_words = sum(c["word_count"] for c in chapters)
    return {
        "title": title, "author": author, "language": lang,
        "summary": desc, "chapters": chapters, "word_count": total_words,
    }


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/upload-epub")
async def upload_epub(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload an EPUB into the library as a hosted story."""
    if not file.filename or not file.filename.lower().endswith(".epub"):
        raise HTTPException(400, "Must be an .epub file")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "EPUB too large (50MB max)")

    parsed = parse_epub(data)

    # Create story
    story_id = uuid.uuid4()
    story = Story(
        id=story_id,
        site=SiteEnum.ao3,                  # using ao3 slot for user-uploaded for now
        site_id=f"upload_{story_id.hex[:12]}",
        url=f"ficatlas://upload/{story_id}",
        title=parsed["title"],
        author=parsed["author"],
        summary=parsed["summary"],
        language=parsed["language"],
        rating=RatingEnum.not_rated,
        status=StatusEnum.complete,
        word_count=parsed["word_count"],
        chapter_count=len(parsed["chapters"]),
        chapter_count_total=len(parsed["chapters"]),
        fandoms=[], characters=[], relationships=[], tags=["user upload"],
        warnings=[], categories=[], genres=[],
        is_hosted=True,
        published_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(story)
    db.flush()

    for ch in parsed["chapters"]:
        db.add(Chapter(
            story_id=story.id,
            number=ch["number"],
            title=ch["title"],
            content=ch["content"],
            word_count=ch["word_count"],
        ))
    db.commit()
    return {"id": str(story.id), "title": parsed["title"], "chapters": len(parsed["chapters"])}


@router.post("/import-url")
async def import_url(url: str = Form(...), db: Session = Depends(get_db)):
    """Fetch a story from AO3/FFnet via FicHub and import it as a hosted story."""
    # First check if we already have it
    existing = db.query(Story).filter(Story.url == url).first()
    if existing and existing.is_hosted:
        return {"id": str(existing.id), "title": existing.title, "already_hosted": True}

    log.info(f"Fetching {url} via FicHub...")
    meta = await fetch_from_fichub(url)
    epub_url = (meta.get("epub_url") or
                meta.get("urls", {}).get("epub"))
    if not epub_url:
        raise HTTPException(502, f"FicHub didn't return an EPUB URL: {meta}")

    epub_bytes = await fetch_epub_bytes(epub_url)
    parsed = parse_epub(epub_bytes)

    # If story already exists in DB (just metadata, not hosted), upgrade it
    if existing:
        existing.is_hosted = True
        existing.summary = existing.summary or parsed["summary"]
        story = existing
        # Clear old chapters if any
        db.query(Chapter).filter(Chapter.story_id == story.id).delete()
    else:
        site = SiteEnum.ao3 if "archiveofourown.org" in url else SiteEnum.ffnet
        site_id = meta.get("id") or url.rstrip("/").split("/")[-1]
        story = Story(
            id=uuid.uuid4(),
            site=site,
            site_id=f"import_{site_id}",
            url=url,
            title=parsed["title"] or meta.get("title") or "Imported",
            author=parsed["author"] or meta.get("author") or "Unknown",
            summary=parsed["summary"] or meta.get("description"),
            language=parsed["language"],
            rating=RatingEnum.not_rated,
            status=StatusEnum.complete,
            word_count=parsed["word_count"],
            chapter_count=len(parsed["chapters"]),
            chapter_count_total=len(parsed["chapters"]),
            fandoms=[], characters=[], relationships=[], tags=["imported"],
            warnings=[], categories=[], genres=[],
            is_hosted=True,
            published_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(story)
    db.flush()

    for ch in parsed["chapters"]:
        db.add(Chapter(
            story_id=story.id, number=ch["number"], title=ch["title"],
            content=ch["content"], word_count=ch["word_count"],
        ))
    db.commit()
    return {"id": str(story.id), "title": story.title, "chapters": len(parsed["chapters"])}


# ── Refresh / discovery ──────────────────────────────────────────────────────

@router.post("/refresh-ao3")
async def refresh_ao3(
    q: Optional[str] = Form(None),
    fandom: Optional[str] = Form(None),
    pages: int = Form(5),
    db: Session = Depends(get_db),
):
    """Force a wider live AO3 fetch for the given query/fandom.
    Useful when the user wants to refresh results beyond what a normal search returns."""
    from live_fetch.ao3_live import fetch_live_ao3
    from live_fetch.persist import persist_live_results

    params = {}
    if q:      params["q"] = q
    if fandom: params["fandoms"] = fandom
    params["sort"] = "updated_desc"

    results = await fetch_live_ao3(params, limit=200, pages=min(pages, 10))
    inserted = persist_live_results(db, results)

    return {
        "fetched": len(results),
        "newly_indexed": inserted,
        "already_known": len(results) - inserted,
    }


@router.get("/can-import")
async def can_import(url: str):
    """Check whether a URL is importable. Returns site + canonical URL."""
    u = url.strip()
    if "archiveofourown.org/works/" in u:
        return {"importable": True, "site": "ao3", "url": u}
    if "fanfiction.net/s/" in u or "fanfiction.net/r/" in u:
        return {"importable": True, "site": "ffnet", "url": u}
    return {"importable": False}
