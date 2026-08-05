"""Library API — downloads, EPUB uploads, hosted stories."""
import os, re, uuid, zipfile, io, logging
import httpx
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Form
from sqlalchemy import func, text as sql_text
from sqlalchemy.orm import Session
from datetime import datetime

from db.session import get_db
from api.auth import (require_admin, require_owner, get_current_user,
                      _require_role)
from models.user import User, ROLE_ADMIN
from models.story import Story, Chapter, SiteEnum, RatingEnum, StatusEnum

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/crawl-status")
def crawl_status(db: Session = Depends(get_db)):
    """Read-only status for the direct-crawl feature so the settings toggle has
    honest feedback: whether it's enabled, and how the last few scheduled crawls
    actually went (so you can see the AO3/FFN blocks rather than guessing)."""
    from api.settings import get_setting
    from models.story import CrawlJob
    enabled = str(get_setting(db, "enable_direct_crawl")).lower() == "true"
    recent = []
    try:
        rows = (db.query(CrawlJob)
                .order_by(CrawlJob.started_at.desc())
                .limit(8).all())
        for j in rows:
            recent.append({
                "site": j.site.value if hasattr(j.site, "value") else str(j.site),
                "status": j.status,
                "found": j.stories_found or 0,
                "new": j.stories_new or 0,
                "error": (j.error or "")[:200],
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            })
    except Exception as e:
        # Job history is decoration on this endpoint; the crawl status below is
        # the part that matters, so a failure here degrades rather than 500s.
        log.debug(f"crawl job history unavailable: {type(e).__name__}")
    # Distinguish a site being genuinely blocked from it just being slow. Failures
    # are now tagged "[blocked]"/"[transient]" by the scheduler; fall back to the
    # old keyword check for any legacy rows without a tag.
    def _is_blocked(j):
        if j["status"] != "failed":
            return False
        e = j["error"].lower()
        if e.startswith("[blocked]"):
            return True
        if e.startswith("[transient]"):
            return False
        return "403" in e or "forbidden" in e or "fallbacks failed" in e
    def _is_slow(j):
        if j["status"] != "failed":
            return False
        e = j["error"].lower()
        return e.startswith("[transient]") or "timeout" in e or "525" in e
    blocked = any(_is_blocked(j) for j in recent)
    slow_only = (not blocked) and any(_is_slow(j) for j in recent)
    # Circuit-breaker state per site (auto-disabled after repeated failures).
    auto_disabled = {
        "ao3":   str(get_setting(db, "crawl_disabled_ao3")).lower() == "true",
        "ffnet": str(get_setting(db, "crawl_disabled_ffnet")).lower() == "true",
    }
    return {"enabled": enabled, "blocked_recently": blocked,
            "slow_recently": slow_only,
            "auto_disabled": auto_disabled, "recent_jobs": recent}


@router.post("/crawl-reset-breaker")
async def crawl_reset_breaker(site: str = Form(...), db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Re-enable a site that the circuit breaker auto-disabled after repeated
    crawl failures. Clears the crawl_disabled_<site> flag."""
    from api.settings import put_setting
    if site not in ("ao3", "ffnet"):
        raise HTTPException(400, "site must be 'ao3' or 'ffnet'")
    put_setting(db, f"crawl_disabled_{site}", "")
    return {"ok": True, "site": site, "re_enabled": True}

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

    # Parse <item> tags — attributes can be in ANY order, so extract id and href separately
    items = {}
    nav_ids = set()
    for item_tag in re.findall(r'<item\b[^>]*>', opf):
        id_m   = re.search(r'\bid="([^"]+)"', item_tag)
        href_m = re.search(r'\bhref="([^"]+)"', item_tag)
        if id_m and href_m:
            items[id_m.group(1)] = href_m.group(1)
            # EPUB 3 marks its navigation document with properties="nav", and
            # that document is usually IN THE SPINE — so the filename filter on
            # the fallback path below never saw it and the book's table of
            # contents was imported as a chapter. 22 stories had one.
            if 'properties="nav"' in item_tag or "properties='nav'" in item_tag:
                nav_ids.add(id_m.group(1))

    base_dir = os.path.dirname(opf_path)
    chapters = []

    # Build the ordered list of hrefs to read
    ordered_hrefs = []
    if spine_ids and items:
        for sid in spine_ids:
            if sid in items and sid not in nav_ids:
                ordered_hrefs.append(items[sid])

    # Fallback: if spine parsing yielded nothing, read all xhtml/html files in zip order
    if not ordered_hrefs:
        ordered_hrefs = [n for n in names
                         if n.lower().endswith((".xhtml", ".html", ".htm"))
                         and "nav" not in n.lower() and "toc" not in n.lower()]

    for idx, href in enumerate(ordered_hrefs):
        # Resolve the file inside the zip
        html = None
        candidates = [
            os.path.join(base_dir, href).replace("\\", "/").replace("./", "") if base_dir else href,
            href,
        ]
        for cand in candidates:
            try:
                html = z.read(cand).decode("utf-8", errors="ignore")
                break
            except KeyError:
                continue
        if html is None:
            # Last resort: suffix match
            for n in names:
                if n.endswith(href.split("/")[-1]):
                    html = z.read(n).decode("utf-8", errors="ignore")
                    break
        if html is None:
            continue

        # Skip front-matter by filename: FicHub/AO3/Calibre EPUBs put the title
        # page, preface, AO3 metadata block, author's-note preamble, dedication,
        # and TOC in separate spine files named like these. These are NOT chapters
        # — saving them was surfacing "author's preliminary notes" as Chapter 1.
        href_lower = href.lower().split("/")[-1]
        if any(marker in href_lower for marker in (
            "titlepage", "title_page", "title-page", "preface", "frontmatter",
            "front_matter", "cover", "nav", "toc", "contents", "dedication",
            "colophon", "copyright", "about", "metadata",
        )):
            continue

        # Extract body content
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        body = body_match.group(1) if body_match else html

        # Pull chapter title
        title_match = re.search(r"<h\d[^>]*>(.*?)</h\d>", body, re.DOTALL)
        ch_title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else None

        # Skip front-matter by heading text: FicHub's preface page often has an
        # <h1> like the work title with a "by Author" line and a summary/notes
        # block rather than a real chapter heading.
        if ch_title and ch_title.strip().lower() in ("preface", "title page", "notes",
                                                      "summary", "tags", "table of contents"):
            continue

        # Strip scripts/styles
        body = re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)

        # Word count
        text_only = re.sub(r"<[^>]+>", " ", body)
        words = len(text_only.split())

        # Skip near-empty files (covers, nav pages)
        if words < 10:
            continue

        # Heuristic: FicHub's first preface page embeds the AO3 metadata block —
        # a cluster of "Rating:", "Fandom:", "Stats:" labels — and author preamble
        # pages carry note markers ("A/N", "please review", "I don't own"). Either,
        # on a short first page, is front matter, not the story.
        if not chapters:  # only scrutinise the candidate first chapter
            meta_labels = ("rating:", "archive warning:", "warning:", "category:",
                           "fandom:", "relationship:", "character:", "additional tags:",
                           "tags:", "stats:", "published:", "completed:", "words:",
                           "chapters:", "summary:")
            note_markers = ("a/n", "author's note", "authors note", "author note",
                            "please review", "please r&r", "updates every",
                            "disclaimer:", "i don't own", "i do not own")
            low = text_only.lower()
            label_hits = sum(1 for lbl in meta_labels if lbl in low)
            note_hit = any(m in low for m in note_markers)
            if ((label_hits >= 4 and words < 600) or
                    (label_hits >= 2 and words < 200) or
                    (note_hit and words < 250)):
                continue

        chapters.append({
            "number": len(chapters) + 1, "title": ch_title, "content": body, "word_count": words,
        })

    total_words = sum(c["word_count"] for c in chapters)

    # Safety net: if aggressive front-matter filtering left us with nothing (e.g.
    # an unusual single-file EPUB), fall back to the old behaviour — read every
    # spine item over 10 words — so we never lose the actual story.
    if not chapters:
        for href in ordered_hrefs:
            html = None
            for cand in [os.path.join(base_dir, href).replace("\\", "/").replace("./", "") if base_dir else href, href]:
                try:
                    html = z.read(cand).decode("utf-8", errors="ignore"); break
                except KeyError:
                    continue
            if html is None:
                continue
            body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
            body = body_match.group(1) if body_match else html
            body = re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
            # Second line of defence: some EPUBs mark the nav document only in
            # the document itself, not in the manifest. A table of contents
            # passes the word-count check easily — it is a list of every chapter
            # title in the book — so it has to be recognised by shape.
            if re.search(r'epub:type=["\']toc["\']|role=["\']doc-toc["\']', body, re.I):
                continue
            title_match = re.search(r"<h\d[^>]*>(.*?)</h\d>", body, re.DOTALL)
            ch_title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else None
            words = len(re.sub(r"<[^>]+>", " ", body).split())
            if words < 10:
                continue
            chapters.append({"number": len(chapters) + 1, "title": ch_title, "content": body, "word_count": words})
        total_words = sum(c["word_count"] for c in chapters)

    return {
        "title": title, "author": author, "language": lang,
        "summary": desc, "chapters": chapters, "word_count": total_words,
    }


# ── Endpoints ───────────────────────────────────────────────────────────────

def _ingest_epub_bytes(data: bytes, db: Session) -> dict:
    """Parse EPUB bytes and insert as a hosted story. Returns dict with id/title/chapters."""
    parsed = parse_epub(data)
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

    return {"id": str(story.id), "title": parsed["title"], "chapters": len(parsed["chapters"])}


@router.post("/upload-epub")
async def upload_epub(file: UploadFile = File(...), db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Upload a single EPUB into the library as a hosted story."""
    if not file.filename or not file.filename.lower().endswith(".epub"):
        raise HTTPException(400, "Must be an .epub file")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "EPUB too large (50MB max)")

    result = _ingest_epub_bytes(data, db)
    db.commit()
    return result


@router.post("/upload-epubs")
async def upload_epubs(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Bulk upload up to 100 EPUBs at once. Each file processed independently;
    failures don't roll back the batch. Returns per-file results.
    """
    if len(files) > 100:
        raise HTTPException(400, "Max 100 files per batch")

    succeeded: list[dict] = []
    failed: list[dict] = []

    for f in files:
        name = f.filename or "(no name)"
        try:
            if not name.lower().endswith(".epub"):
                failed.append({"filename": name, "error": "Not an .epub file"})
                continue

            data = await f.read()
            if len(data) > 50 * 1024 * 1024:
                failed.append({"filename": name, "error": "Too large (50MB max)"})
                continue

            result = _ingest_epub_bytes(data, db)
            db.commit()
            succeeded.append({"filename": name, **result})
        except Exception as e:
            db.rollback()
            failed.append({"filename": name, "error": str(e)[:200]})

    return {
        "total":    len(files),
        "succeeded": len(succeeded),
        "failed":    len(failed),
        "results":   succeeded,
        "errors":    failed,
    }


# How many private imports one reader may hold. Not a licensing position — a
# reader importing a work they could read on AO3 anyway is not obviously worse
# than the browser cache — but an unbounded per-user import button is a way for
# anyone to fill this disk and this bandwidth, and the answer to "how many is
# reasonable for personal reading" is not "unlimited".
PRIVATE_IMPORT_QUOTA = int(os.getenv("PRIVATE_IMPORT_QUOTA", "50"))


@router.post("/import-url")
async def import_url(url: str = Form(...), private: bool = Form(False),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user),
):
    """Fetch a story from AO3/FFnet via FicHub and import it.

    Two quite different operations behind one endpoint, separated by `private`:

      private=false  Admin only. Adds the story to the SHARED index, readable by
                     everyone. This is publishing someone else's work under this
                     site's name, which is why it stays restricted.

      private=true   Any signed-in reader. The story and its chapters go into
                     the same shared tables — so dedup and cross-post matching
                     still see them — but `is_hosted` stays false and the reader
                     gets a row in `user_hosted`. Only they can read it.

    The distinction is the whole point of allowing reader imports at all: the
    risk was never "is this fanfiction", it was "did the author agree to it
    being republished here". A private import republishes nothing.
    """
    if private:
        if viewer is None:
            raise HTTPException(401, "Sign in to import a story to your own library.")
        held = db.execute(sql_text(
            "SELECT count(*) FROM user_hosted WHERE user_id = :u"
        ), {"u": str(viewer.id)}).scalar() or 0
        if held >= PRIVATE_IMPORT_QUOTA:
            raise HTTPException(
                429,
                f"You have {held} stories in your library, which is the limit "
                f"of {PRIVATE_IMPORT_QUOTA}. Remove one to import another.",
            )
    else:
        _require_role(viewer, db, ROLE_ADMIN)
    # Metadata-only seed rows carry a synthetic seed:// URL with no page behind it.
    # The UI hides the import button for these, but reject them here too so a stale
    # client can't send FicHub a URL it will only fail on.
    if url.startswith("seed://"):
        raise HTTPException(
            400,
            "This is a metadata-only entry with no source page. "
            "Use the AO3 search link on the story to find the work itself.",
        )

    # Check exact URL match first
    existing = db.query(Story).filter(Story.url == url).first()
    # ...then check if any existing story knows this URL as a cross-post
    if not existing:
        existing = db.query(Story).filter(
            Story.cross_post_urls.any(url)  # type: ignore[arg-type]
        ).first()
    if existing and existing.is_hosted:
        ch_count = db.query(Chapter).filter(Chapter.story_id == existing.id).count()
        return {"id": str(existing.id), "title": existing.title,
                "chapters": ch_count, "already_hosted": True,
                "matched_via": "cross_post" if existing.url != url else "url"}

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
        # A private import must never flip a story onto the public shelf: that
        # would publish it for everybody on one reader's action.
        if not private:
            existing.is_hosted = True
        existing.summary = existing.summary or parsed["summary"]
        story = existing
        # Clear old chapters if any
        db.query(Chapter).filter(Chapter.story_id == story.id).delete()
    else:
        site = SiteEnum.ao3 if "archiveofourown.org" in url else SiteEnum.ffnet
        # The archive's own id, not the URL's trailing slug. Taking the last
        # path segment turned ".../s/12792189/1/A-Beautiful-Lie" into
        # "import_A-Beautiful-Lie", which no id-based lookup can ever match —
        # so those rows were invisible to DLP tagging, cross-post detection and
        # dedup, and excluded from every enrichment queue (they all require a
        # numeric site_id). Only fall back to the slug when the URL carries no
        # id at all, e.g. a bare EPUB upload.
        key = _story_key_from_url(url)
        site_id = key[1] if key else (meta.get("id") or url.rstrip("/").split("/")[-1])
        story = Story(
            id=uuid.uuid4(),
            site=site,
            site_id=site_id,
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
            # A private import is not on the public shelf. The row and its
            # chapters exist in the shared tables so dedup and cross-post
            # matching still see them; only the grant below opens the text.
            is_hosted=not private,
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
    if private and viewer is not None:
        db.flush()   # story.id must exist before the grant references it
        db.execute(sql_text("""
            INSERT INTO user_hosted (user_id, story_id) VALUES (:u, :s)
            ON CONFLICT DO NOTHING
        """), {"u": str(viewer.id), "s": str(story.id)})

    db.commit()
    return {"id": str(story.id), "title": story.title,
            "chapters": len(parsed["chapters"]), "private": bool(private)}


# ── Refresh / discovery ──────────────────────────────────────────────────────

@router.post("/refresh-ao3")
async def refresh_ao3(
    q: Optional[str] = Form(None),
    fandom: Optional[str] = Form(None),
    pages: int = Form(5),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
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
def can_import(url: str):
    """Check whether a URL is importable. Returns site + canonical URL."""
    u = url.strip()
    if "archiveofourown.org/works/" in u:
        return {"importable": True, "site": "ao3", "url": u}
    if "fanfiction.net/s/" in u or "fanfiction.net/r/" in u:
        return {"importable": True, "site": "ffnet", "url": u}
    return {"importable": False}


@router.get("/hosted")
def list_hosted(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    """Stories hosted on FicAtlas (EPUB uploads + URL imports), newest first.

    Returns the TOTAL alongside the page. The endpoint always supported offset,
    but returned a bare list, so the library showed the first 100 of 29,977 and
    labelled its tab "100" — the page size presented as the whole shelf, with
    the other 29,877 unreachable.
    """
    total = db.query(func.count(Story.id)).filter(Story.is_hosted == True).scalar() or 0
    q = (db.query(Story)
         .filter(Story.is_hosted == True)
         .order_by(Story.indexed_at.desc())
         .offset(offset).limit(min(limit, 200)))
    rows = q.all()
    items = [
        {
            "id": str(s.id),
            "title": s.title,
            "author": s.author or "Unknown",
            "site": s.site.value if s.site else "ao3",
            "word_count": s.word_count or 0,
            "chapter_count": s.chapter_count or 0,
            "summary": s.summary,
            "tags": s.tags or [],
            "indexed_at": s.indexed_at.isoformat() if s.indexed_at else None,
        }
        for s in rows
    ]
    return {"total": total, "offset": offset, "limit": len(items), "items": items}


# ── the reader's own shelf ──────────────────────────────────────────────────

@router.get("/mine")
def list_mine(limit: int = 100, offset: int = 0,
                    db: Session = Depends(get_db),
                    viewer: Optional[User] = Depends(get_current_user)):
    """Stories this reader imported privately — theirs alone, newest first.

    The counterpart to /hosted. /hosted is the public shelf; this is the shelf
    behind your own door, and the two never mix: a row here has is_hosted=false,
    so it is invisible to search and unreadable by anyone else including admins
    (see may_read_text in stories.py, which checks ownership rather than role).

    Unauthenticated gets an empty shelf rather than a 401. The Library page
    renders this tab for everyone, and a signed-out visitor should see "nothing
    here yet" instead of an error — they have not done anything wrong.
    """
    if viewer is None:
        return {"total": 0, "offset": 0, "limit": 0, "items": []}

    total = db.execute(sql_text(
        "SELECT count(*) FROM user_hosted WHERE user_id = :u"
    ), {"u": str(viewer.id)}).scalar() or 0

    rows = db.execute(sql_text("""
        SELECT s.id, s.title, s.author, s.site, s.word_count, s.chapter_count,
               s.summary, s.tags, s.url, uh.created_at
        FROM user_hosted uh JOIN stories s ON s.id = uh.story_id
        WHERE uh.user_id = :u
        ORDER BY uh.created_at DESC
        OFFSET :off LIMIT :lim
    """), {"u": str(viewer.id), "off": offset, "lim": min(limit, 200)}).fetchall()

    return {
        "total": total, "offset": offset, "limit": len(rows),
        "items": [{
            "id": str(r[0]), "title": r[1], "author": r[2] or "Unknown",
            "site": (r[3].value if hasattr(r[3], "value") else r[3]) or "ao3",
            "word_count": r[4] or 0, "chapter_count": r[5] or 0,
            "summary": r[6], "tags": r[7] or [], "url": r[8],
            "added_at": r[9].isoformat() if r[9] else None,
        } for r in rows],
    }


@router.delete("/mine/{story_id}")
def remove_mine(story_id: str, db: Session = Depends(get_db),
                      viewer: Optional[User] = Depends(get_current_user)):
    """Give up your copy of a story.

    Drops the user_hosted row only. The story and its chapters stay in the
    shared tables, because dedup and cross-post matching depend on them and
    another reader may hold the same import. Nothing is deleted that anyone
    else's access relies on, and nothing of yours survives.
    """
    if viewer is None:
        raise HTTPException(401, "Sign in to manage your library.")
    res = db.execute(sql_text(
        "DELETE FROM user_hosted WHERE user_id = :u AND story_id = :s"
    ), {"u": str(viewer.id), "s": story_id})
    db.commit()
    if not res.rowcount:
        raise HTTPException(404, "That story is not in your library.")
    return {"ok": True, "removed": story_id}


# ── AO3 Atom feed discovery (the reliable fresh-data path) ───────────────────

@router.post("/poll-feed")
async def poll_feed(
    fandom: str = Form(...),
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Resolve a fandom name to its AO3 canonical tag feed, poll it, and index new works.
    Optional post-filters (min_words/max_words/complete_only) narrow the 25-entry feed.
    """
    from live_fetch.ao3_feeds import resolve_tag_id, fetch_feed, filter_entries
    from live_fetch.persist import persist_live_results
    import httpx

    async with httpx.AsyncClient(
        headers={"User-Agent": "FicAtlasBot/1.0 (+fanfic discovery)"},
        timeout=20, follow_redirects=True
    ) as client:
        tag_id = await resolve_tag_id(client, fandom)

    if not tag_id:
        return {"ok": False, "error": f"Couldn't resolve a canonical AO3 tag feed for '{fandom}'. "
                                       "Only canonical fandom/character/relationship tags have feeds."}

    entries = await fetch_feed(tag_id, limit=25)
    raw_count = len(entries)
    entries = filter_entries(
        entries,
        min_words=min_words, max_words=max_words, complete_only=complete_only,
    )
    inserted = persist_live_results(db, entries)

    return {
        "ok": True,
        "fandom": fandom,
        "tag_id": tag_id,
        "found": raw_count,
        "after_filter": len(entries),
        "newly_indexed": inserted,
    }


# ── On-load auto poll (debounced) ────────────────────────────────────────────

_last_autopoll = {"at": None}

@router.post("/autopoll")
async def autopoll(db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Called by the frontend on page load. Polls the tracked fandom's AO3 feed,
    but debounced server-side to at most once every 10 minutes so refreshing
    the page repeatedly doesn't hammer AO3.
    """
    from datetime import datetime, timezone, timedelta
    from api.settings import get_setting
    from live_fetch.ao3_feeds import resolve_tag_id, fetch_feed, filter_entries
    from live_fetch.persist import persist_live_results
    import httpx

    now = datetime.now(timezone.utc)
    last = _last_autopoll["at"]
    if last and (now - last) < timedelta(minutes=10):
        return {"ok": True, "skipped": "debounced", "next_in_seconds": int(600 - (now - last).total_seconds())}

    fandom = get_setting(db, "tracked_fandom")
    if not fandom:
        return {"ok": False, "error": "No tracked fandom set"}

    # Read filter settings (all optional)
    def _int_setting(key: str) -> int | None:
        v = (get_setting(db, key) or "").strip()
        try: return int(v) if v else None
        except Exception: return None
    min_words     = _int_setting("feed_min_words")
    max_words     = _int_setting("feed_max_words")
    complete_only = (get_setting(db, "feed_complete_only") or "false").lower() == "true"

    _last_autopoll["at"] = now

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "FicAtlasBot/1.0 (+fanfic discovery)"},
            timeout=20, follow_redirects=True
        ) as client:
            tag_id = await resolve_tag_id(client, fandom.split(",")[0].strip())
        if not tag_id:
            return {"ok": False, "error": f"No canonical feed for '{fandom}'"}

        entries = await fetch_feed(tag_id, limit=25)
        raw_count = len(entries)
        entries = filter_entries(entries, min_words=min_words, max_words=max_words, complete_only=complete_only)
        inserted = persist_live_results(db, entries)
        return {"ok": True, "fandom": fandom, "found": raw_count,
                "after_filter": len(entries), "newly_indexed": inserted}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Delete hosted stories ────────────────────────────────────────────────────

@router.delete("/hosted/{story_id}")
def delete_hosted(story_id: str, db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Delete a hosted story and its chapters from the library."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")
    # Only allow deleting hosted stories (imports/uploads), not the bulk-indexed archive
    if not story.is_hosted:
        raise HTTPException(403, "Can only delete hosted stories (imports and uploads)")

    db.query(Chapter).filter(Chapter.story_id == story.id).delete()
    db.delete(story)
    db.commit()
    return {"ok": True, "deleted": story_id}


# ── FF.net discovery via Wayback Machine ─────────────────────────────────────

@router.post("/discover-ffnet")
async def discover_ffnet(
    query: Optional[str] = Form(None),
    since: str = Form("20230101"),
    limit: int = Form(50),
    auto_import: bool = Form(False),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Discover FFN story URLs via the Wayback Machine CDX index (no Cloudflare).
    Returns the URL list. If auto_import=true, also pulls each via FicHub (slow).
    """
    from live_fetch.ffnet_wayback import discover_ffn_urls
    urls = await discover_ffn_urls(query=query, since=since, limit=limit)

    if not auto_import:
        return {"ok": True, "found": len(urls), "urls": urls, "imported": 0}

    # Auto-import each — slow because FicHub is serial-only
    imported, failed = 0, 0
    for u in urls:
        try:
            meta = await fetch_from_fichub(u["url"])
            epub_url = meta.get("epub_url") or meta.get("urls", {}).get("epub")
            if not epub_url:
                failed += 1
                continue
            epub_bytes = await fetch_epub_bytes(epub_url)
            _ingest_epub_from_url(db, u["url"], epub_bytes, SiteEnum.ffnet,
                                  provenance=["imported", "via_wayback"])
            db.commit()
            imported += 1
        except Exception as e:
            db.rollback()
            log.warning(f"Auto-import {u['url']} failed: {e}")
            failed += 1

    return {"ok": True, "found": len(urls), "urls": urls, "imported": imported, "failed": failed}


def _ingest_epub_from_url(db: Session, url: str, epub_bytes: bytes, site: SiteEnum,
                          provenance: list[str] | None = None) -> dict:
    """Helper: parse epub bytes and persist as a hosted story for a given URL.
    `provenance` controls the marker tags on a newly created Story row
    (e.g. ["imported", "via_dlp"] vs ["imported", "via_wayback"]).
    """
    if provenance is None:
        provenance = ["imported"]
    parsed = parse_epub(epub_bytes)
    existing = db.query(Story).filter(Story.url == url).first()
    if existing:
        existing.is_hosted = True
        db.query(Chapter).filter(Chapter.story_id == existing.id).delete()
        story = existing
    else:
        # Same as above: prefer the archive's id so the row can be matched.
        key = _story_key_from_url(url)
        site_id = key[1] if key else f"import_{url.rstrip('/').split('/')[-1]}"
        story = Story(
            id=uuid.uuid4(), site=site, site_id=site_id, url=url,
            title=parsed["title"] or "Imported", author=parsed["author"] or "Unknown",
            summary=parsed["summary"], language=parsed["language"],
            rating=RatingEnum.not_rated, status=StatusEnum.complete,
            word_count=parsed["word_count"], chapter_count=len(parsed["chapters"]),
            chapter_count_total=len(parsed["chapters"]),
            fandoms=[], characters=[], relationships=[],
            tags=list(provenance),
            warnings=[], categories=[], genres=[],
            is_hosted=True,
            published_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(story)
    db.flush()
    for ch in parsed["chapters"]:
        db.add(Chapter(
            story_id=story.id, number=ch["number"], title=ch["title"],
            content=ch["content"], word_count=ch["word_count"],
        ))
    return {"id": str(story.id), "title": story.title, "chapters": len(parsed["chapters"])}


# ── DLP (DarkLordPotter) library discovery ───────────────────────────────────


def _story_key_from_url(url: str) -> tuple[str, str] | None:
    """(site, site_id) for an FFN/AO3 story URL, ignoring scheme and host form.

    DLP publishes "http://www.fanfiction.net/s/3639659/1/" while the importers
    normalised the same story to "https://www.fanfiction.net/s/3639659/1/", so
    matching on the URL string found nothing — 484 of DLP's 630 curated stories
    were already indexed and only 20 carried the tag.
    """
    m = re.search(r"fanfiction\.net/s/(\d+)", url or "")
    if m:
        return ("ffnet", m.group(1))
    m = re.search(r"archiveofourown\.org/works/(\d+)", url or "")
    if m:
        return ("ao3", m.group(1))
    return None


def _find_indexed_story(db: Session, urls: list[str]):
    """Find an already-indexed story for any of these URLs, by site + id."""
    for u in urls:
        key = _story_key_from_url(u)
        if not key:
            continue
        hit = (db.query(Story)
               .filter(Story.site == SiteEnum(key[0]), Story.site_id == key[1])
               .first())
        if hit:
            return hit
    return None


@router.post("/discover-dlp")
async def discover_dlp(
    corpus: str = Form("hp"),            # "hp" or "other"
    limit: int = Form(200),
    auto_import: bool = Form(False),
    prefer: str = Form("ao3"),           # which URL to import: "ao3" or "ffn"
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Scrape the DLP library list. Returns parsed entries with FFN/AO3/etc URLs.
    If auto_import=true, also imports each entry's preferred external URL via FicHub
    and merges the DLP-curated tags onto the resulting Story row.
    """
    from live_fetch.dlp_scraper import fetch_dlp_library

    entries = await fetch_dlp_library(corpus=corpus, limit=limit)

    if not entries:
        return {"ok": False, "error": "Couldn't fetch the DLP library list. Try again later."}

    # Tag the stories we ALREADY have, before considering any download.
    #
    # 484 of DLP's 630 curated works are already in the index from the bulk
    # dumps; they simply weren't tagged, because the old matcher compared full
    # URL strings and DLP's are http:// while ours are https://. Merging the
    # curation onto existing rows costs a few hundred indexed lookups and needs
    # no FicHub fetch at all — which is what made this feature look broken, with
    # only 20 of them tagged.
    tagged = 0
    for e in entries:
        cand = [v for k, v in (e.get("urls") or {}).items() if k in ("ffn", "ao3")]
        if not cand:
            continue
        hit = _find_indexed_story(db, cand)
        if hit is not None:
            before = set(hit.tags or [])
            _merge_dlp_tags(hit, e.get("dlp_tags") or [])
            _record_cross_posts(hit, cand)
            if set(hit.tags or []) != before:
                tagged += 1
    if tagged:
        db.commit()

    if not auto_import:
        return {"ok": True, "found": len(entries), "tagged_existing": tagged,
                "entries": entries, "imported": 0}

    # Auto-import flow: per entry, pick a URL and import it via FicHub
    fallbacks = [prefer, "ao3", "ffn", "patronuscharm", "ficwad", "hpfanficarchive"]
    imported, failed, skipped = 0, 0, 0

    for e in entries:
        chosen_url = None
        for kind in fallbacks:
            if kind in e["urls"]:
                chosen_url = e["urls"][kind]; break
        if not chosen_url:
            skipped += 1; continue

        # FicHub only handles FFN and AO3 reliably. Skip others.
        if not ("fanfiction.net/" in chosen_url or "archiveofourown.org/" in chosen_url):
            skipped += 1; continue

        try:
            # Build the set of "this is the same story" URLs DLP gave us
            sibling_urls = [v for k, v in e["urls"].items() if k in ("ffn", "ao3") and v != chosen_url]

            # Is this story (or any of its siblings) already in our index?
            candidates = [chosen_url] + sibling_urls
            # Match on site + story id, not the raw URL: DLP's links are http://
            # and ours are https://, so a string comparison never matched.
            existing = _find_indexed_story(db, candidates)
            if not existing:
                existing = db.query(Story).filter(Story.url.in_(candidates)).first()
            if not existing:
                # Also check cross_post_urls of existing stories
                for u in candidates:
                    existing = db.query(Story).filter(
                        Story.cross_post_urls.any(u)  # type: ignore[arg-type]
                    ).first()
                    if existing: break

            if existing and existing.is_hosted:
                # Already imported under some URL — merge DLP tags and record any
                # cross-posts we hadn't seen before, but don't re-fetch the EPUB.
                _merge_dlp_tags(existing, e["dlp_tags"])
                _record_cross_posts(existing, candidates)
                db.commit()
                skipped += 1
                continue

            meta = await fetch_from_fichub(chosen_url)
            epub_url = meta.get("epub_url") or meta.get("urls", {}).get("epub")
            if not epub_url:
                failed += 1; continue
            epub_bytes = await fetch_epub_bytes(epub_url)

            site = SiteEnum.ao3 if "archiveofourown.org" in chosen_url else SiteEnum.ffnet
            result = _ingest_epub_from_url(db, chosen_url, epub_bytes, site,
                                           provenance=["imported", "via_dlp"])

            # Merge DLP tags AND record cross-posts onto the newly imported story
            story = db.query(Story).get(uuid.UUID(result["id"]))
            if story:
                _merge_dlp_tags(story, e["dlp_tags"])
                _record_cross_posts(story, sibling_urls)
            db.commit()
            imported += 1
        except Exception as ex:
            db.rollback()
            log.warning(f"DLP auto-import failed for {chosen_url}: {ex}")
            failed += 1

    return {
        "ok": True, "found": len(entries),
        "imported": imported, "failed": failed, "skipped": skipped,
        "entries": entries,
    }


def _merge_dlp_tags(story: "Story", dlp_tags: list[str]) -> None:
    """Add DLP-curated tags to a story's existing tags array (deduped)."""
    # Filter noise: skip pure author-attribution tags ('author:xxx') from DLP
    clean = [t for t in dlp_tags if not t.lower().startswith("author")]
    if not clean: return
    existing = set((story.tags or []))
    merged = list(existing | set(clean))
    story.tags = merged + ["dlp_library"] if "dlp_library" not in merged else merged


def _record_cross_posts(story: "Story", sibling_urls: list[str]) -> None:
    """Append known same-story URLs from other sites onto the Story row."""
    if not sibling_urls: return
    current = set(story.cross_post_urls or [])
    # Don't include the story's own canonical URL
    new_urls = [u for u in sibling_urls if u and u != story.url and u not in current]
    if new_urls:
        story.cross_post_urls = list(current | set(new_urls))


# ── AO3 deep-filter discovery (paginated tag-works scrape) ───────────────────

@router.post("/discover-ao3")
async def discover_ao3(
    fandom: str = Form(...),
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    sort: str = Form("revised_at"),
    direction: str = Form("desc"),
    ratings: Optional[str] = Form(None),       # comma-separated, e.g. "T,M,E"
    excluded_tags: Optional[str] = Form(None), # comma-separated
    max_pages: int = Form(5),                  # 5 pages = ~100 works,
    _admin=Depends(require_admin),
):
    """
    Kick off a deep AO3 discovery as an ASYNC JOB. Returns immediately with a
    job_id; poll `GET /api/library/jobs/{job_id}` for progress. The job runs
    until done. This decouples the long-running scrape (10-60s) from the HTTP
    request so it survives any proxy / Tailscale / Next.js timeout.
    """
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results
    from live_fetch.jobs import new_job, run_in_background
    from live_fetch.ao3_feeds import ao3_cooldown_remaining
    from db.session import db_session

    # Short-circuit if AO3 just blanket-blocked us — don't start a doomed job.
    cooldown = ao3_cooldown_remaining()
    if cooldown > 0:
        raise HTTPException(
            429,
            f"AO3 is currently blocking us ({int(cooldown)}s cooldown remaining). "
            f"AO3's Cloudflare intermittently blocks datacenter IPs. Wait it out "
            f"or POST /api/library/admin/clear-ao3-cooldown to force-retry."
        )

    ratings_list = [r.strip().upper() for r in (ratings or "").split(",") if r.strip()] or None
    excluded_tags_list = [t.strip() for t in (excluded_tags or "").split(",") if t.strip()] or None
    capped_pages = max(1, min(max_pages, 20))   # 20 pages = ~400 works

    job_id, state = new_job("discover-ao3")
    state["fandom"] = fandom
    state["max_pages"] = capped_pages

    async def _run():
        try:
            def on_progress(snap):
                state.update(snap)
            result = await scrape_tag_works(
                fandom,
                min_words=min_words, max_words=max_words,
                complete_only=complete_only, sort=sort, direction=direction,
                ratings=ratings_list, excluded_tags=excluded_tags_list,
                max_pages=capped_pages, on_progress=on_progress,
            )
            entries = result["entries"]
            state["pages_ok"]     = result["pages_ok"]
            state["pages_failed"] = result["pages_failed"]
            state["tried_url"]    = result["tried_url"]

            if result["pages_failed"] > 0 and result["pages_ok"] == 0:
                state["status"]   = "error"
                state["error"]    = (
                    f"AO3 didn't respond within timeout (likely throttling our IP — "
                    f"this is intermittent for datacenter IPs). Try again in a few minutes, "
                    f"or try a different fandom. URL tried: {result['tried_url']}"
                )
                state["progress"] = "AO3 fetches all failed (likely throttled)"
            else:
                state["progress"] = f"Persisting {len(entries)} works to DB…"
                with db_session() as db:
                    inserted = persist_live_results(db, entries)
                state["found"]         = len(entries)
                state["newly_indexed"] = inserted
                state["status"]        = "done"
                state["progress"]      = f"Done — {len(entries)} found, {inserted} new"
        except Exception as e:
            log.exception("discover-ao3 job failed")
            state["status"]   = "error"
            state["error"]    = f"{e.__class__.__name__}: {e}"
            state["progress"] = "Crashed — see backend log"
        finally:
            state["finished_at"] = datetime.utcnow().isoformat()

    run_in_background(_run)
    return {"ok": True, "job_id": job_id}


# ── HPFFA via the AO3 Open Doors collection ──────────────────────────────────

@router.post("/discover-hpffa")
async def discover_hpffa(
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    sort: str = Form("revised_at"),
    max_pages: int = Form(5),
    _admin=Depends(require_admin),
):
    """
    Pulls stories from the HPFFA Open Doors collection on AO3 as an async job.
    Same pattern as discover-ao3 — returns job_id, poll /library/jobs/{id}.
    """
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results
    from live_fetch.jobs import new_job, run_in_background
    from live_fetch.ao3_feeds import ao3_cooldown_remaining
    from db.session import db_session

    cooldown = ao3_cooldown_remaining()
    if cooldown > 0:
        raise HTTPException(
            429,
            f"AO3 is currently blocking us ({int(cooldown)}s cooldown remaining). "
            f"HPFFA discovery hits the AO3 Open Doors collection, so it's affected too. "
            f"Wait it out or clear via /api/library/admin/clear-ao3-cooldown."
        )

    capped_pages = max(1, min(max_pages, 20))
    job_id, state = new_job("discover-hpffa")
    state["max_pages"] = capped_pages

    async def _run():
        try:
            def on_progress(snap):
                state.update(snap)
            result = await scrape_tag_works(
                tag="", min_words=min_words, max_words=max_words,
                complete_only=complete_only, sort=sort,
                max_pages=capped_pages, collection="hpfanfiction_hpff",
                on_progress=on_progress,
            )
            entries = result["entries"]
            state["pages_ok"]     = result["pages_ok"]
            state["pages_failed"] = result["pages_failed"]

            if result["pages_failed"] > 0 and result["pages_ok"] == 0:
                state["status"]   = "error"
                state["error"]    = "AO3 unreachable"
                state["progress"] = "Failed"
            else:
                state["progress"] = f"Persisting {len(entries)} works…"
                # Tag provenance
                for e in entries:
                    e["tags"] = list(set((e.get("tags") or []) + ["hpffa_archive"]))
                with db_session() as db:
                    inserted = persist_live_results(db, entries)
                state["found"]         = len(entries)
                state["newly_indexed"] = inserted
                state["status"]        = "done"
                state["progress"]      = f"Done — {len(entries)} found, {inserted} new"
        except Exception as e:
            log.exception("discover-hpffa job failed")
            state["status"]   = "error"
            state["error"]    = f"{e.__class__.__name__}: {e}"
            state["progress"] = "Crashed — see backend log"
        finally:
            state["finished_at"] = datetime.utcnow().isoformat()

    run_in_background(_run)
    return {"ok": True, "job_id": job_id}


# ── HexFiles (Harry Potter FanFic Archive) via AO3 Open Doors ────────────────

@router.post("/discover-hexfiles")
async def discover_hexfiles(
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    sort: str = Form("revised_at"),
    max_pages: int = Form(5),
    _admin=Depends(require_admin),
):
    """Pull stories from the Harry Potter FanFic Archive ("the HexFiles") Open
    Doors collection on AO3. This is a SEPARATE ~18k-member archive from HPFFA —
    it was run by Chad (CazBandit), moved to AO3 in Nov 2021 under the collection
    slug `harrypotterfanficarchive`. Async job; poll /library/jobs/{id}."""
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results
    from live_fetch.jobs import new_job, run_in_background
    from live_fetch.ao3_feeds import ao3_cooldown_remaining
    from db.session import db_session

    cooldown = ao3_cooldown_remaining()
    if cooldown > 0:
        raise HTTPException(
            429,
            f"AO3 is currently blocking us ({int(cooldown)}s cooldown remaining). "
            f"HexFiles discovery hits the AO3 Open Doors collection, so it's affected too."
        )

    capped_pages = max(1, min(max_pages, 20))
    job_id, state = new_job("discover-hexfiles")
    state["max_pages"] = capped_pages

    async def _run():
        try:
            def on_progress(snap):
                state.update(snap)
            result = await scrape_tag_works(
                tag="", min_words=min_words, max_words=max_words,
                complete_only=complete_only, sort=sort,
                max_pages=capped_pages, collection="harrypotterfanficarchive",
                on_progress=on_progress,
            )
            entries = result["entries"]
            state["pages_ok"]     = result["pages_ok"]
            state["pages_failed"] = result["pages_failed"]

            if result["pages_failed"] > 0 and result["pages_ok"] == 0:
                state["status"]   = "error"
                state["error"]    = "AO3 unreachable"
                state["progress"] = "Failed"
            else:
                state["progress"] = f"Persisting {len(entries)} works…"
                for e in entries:
                    e["tags"] = list(set((e.get("tags") or []) + ["hexfiles_archive"]))
                with db_session() as db:
                    inserted = persist_live_results(db, entries)
                state["found"]         = len(entries)
                state["newly_indexed"] = inserted
                state["status"]        = "done"
                state["progress"]      = f"Done — {len(entries)} found, {inserted} new"
        except Exception as e:
            log.exception("discover-hexfiles job failed")
            state["status"]   = "error"
            state["error"]    = f"{e.__class__.__name__}: {e}"
            state["progress"] = "Crashed — see backend log"
        finally:
            state["finished_at"] = datetime.utcnow().isoformat()

    run_in_background(_run)
    return {"ok": True, "job_id": job_id}


# ── SquidgeWorld archive (Otwarchive software, ~30k mostly-HP works) ──────────

@router.post("/discover-squidgeworld")
async def discover_squidgeworld(
    fandom: str = Form("Harry Potter - J. K. Rowling"),
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    sort: str = Form("revised_at"),
    max_pages: int = Form(5),
    _admin=Depends(require_admin),
):
    """Scrape SquidgeWorld Archive (squidgeworld.org). It runs the same OTW
    'Otwarchive' software as AO3, so its /works listing shares AO3's HTML
    structure — we reuse the AO3 works scraper pointed at the SquidgeWorld host.
    Async job; poll /library/jobs/{id}."""
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results
    from live_fetch.jobs import new_job, run_in_background
    from db.session import db_session

    capped_pages = max(1, min(max_pages, 20))
    job_id, state = new_job("discover-squidgeworld")
    state["max_pages"] = capped_pages

    async def _run():
        try:
            def on_progress(snap):
                state.update(snap)
            result = await scrape_tag_works(
                tag=fandom, min_words=min_words, max_words=max_words,
                complete_only=complete_only, sort=sort,
                max_pages=capped_pages,
                base_url="https://squidgeworld.org",
                on_progress=on_progress,
            )
            entries = result["entries"]
            state["pages_ok"]     = result["pages_ok"]
            state["pages_failed"] = result["pages_failed"]

            if result["pages_failed"] > 0 and result["pages_ok"] == 0:
                state["status"]   = "error"
                state["error"]    = "SquidgeWorld unreachable"
                state["progress"] = "Failed"
            else:
                state["progress"] = f"Persisting {len(entries)} works…"
                for e in entries:
                    e["tags"] = list(set((e.get("tags") or []) + ["squidgeworld_archive"]))
                with db_session() as db:
                    inserted = persist_live_results(db, entries)
                state["found"]         = len(entries)
                state["newly_indexed"] = inserted
                state["status"]        = "done"
                state["progress"]      = f"Done — {len(entries)} found, {inserted} new"
        except Exception as e:
            log.exception("discover-squidgeworld job failed")
            state["status"]   = "error"
            state["error"]    = f"{e.__class__.__name__}: {e}"
            state["progress"] = "Crashed — see backend log"
        finally:
            state["finished_at"] = datetime.utcnow().isoformat()

    run_in_background(_run)
    return {"ok": True, "job_id": job_id}


# ── Cross-post dedup (one-shot batch over existing data) ─────────────────────

@router.post("/dedup-crossposts")
def dedup_crossposts(limit: Optional[int] = Form(None),
    dry_run: bool = Form(False),
    _admin=Depends(require_admin),
):
    """Scan existing stories and merge cross-posted copies (same title+author on
    different sites) into single canonical rows, recording the alternates in
    cross_post_urls and keeping the most-recently-updated copy's hosted text.
    Async job; poll /library/jobs/{id}."""
    from live_fetch.jobs import new_job, run_in_background
    from live_fetch.crosspost import group_existing, merge_group
    from db.session import db_session

    job_id, state = new_job("dedup-crossposts")

    async def _run():
        try:
            state["progress"] = "Scanning for cross-posted works…"
            with db_session() as db:
                groups = group_existing(db, limit=limit)
                state["groups_found"] = len(groups)

                # merge_group DELETES every non-canonical row in a group, so this
                # batch is irreversible. dry_run reports exactly what would be
                # merged — and a sample of it — without touching anything.
                if dry_run:
                    state["sample"] = [
                        {
                            "title": g[0].title,
                            "author": g[0].author,
                            "copies": len(g),
                            "sites": sorted({s2.site.value for s2 in g}),
                            "urls": [s2.url for s2 in g][:4],
                        }
                        for g in groups[:25]
                    ]
                    state["would_merge"] = sum(len(g) - 1 for g in groups)
                    state["status"] = "done"
                    state["progress"] = (
                        f"Dry run — would merge {state['would_merge']} rows "
                        f"across {len(groups)} works. Nothing changed."
                    )
                    return

                merged = 0
                for i, group in enumerate(groups):
                    try:
                        merge_group(db, group)
                        db.commit()
                        merged += len(group) - 1
                    except Exception:
                        db.rollback()
                    if i % 50 == 0:
                        state["progress"] = f"Merged {merged} duplicates across {i}/{len(groups)} groups…"
                state["duplicates_merged"] = merged
                state["status"]   = "done"
                state["progress"] = f"Done — merged {merged} duplicate rows across {len(groups)} works"
        except Exception as e:
            log.exception("dedup-crossposts job failed")
            state["status"]   = "error"
            state["error"]    = f"{e.__class__.__name__}: {e}"
            state["progress"] = "Crashed — see backend log"
        finally:
            state["finished_at"] = datetime.utcnow().isoformat()

    run_in_background(_run)
    return {"ok": True, "job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    """Poll for an async discover-* job. Returns 404 once the job has aged out
    of the in-memory store (~15 min after completion)."""
    from live_fetch.jobs import get_job
    state = get_job(job_id)
    if not state:
        raise HTTPException(404, "Job not found or has aged out (jobs are retained 15 min after completion)")
    return state


@router.get("/ao3-status")
def ao3_status():
    """Return current AO3 reachability state — whether we're in a cooldown
    after repeated failures, and how long until we'll retry. The UI uses
    this to grey out the AO3-dependent buttons when AO3 is unreachable."""
    from live_fetch.ao3_feeds import ao3_cooldown_remaining
    cd = ao3_cooldown_remaining()
    return {
        "cooldown_active":    cd > 0,
        "cooldown_remaining": int(cd),
        "message": (
            f"AO3 unreachable — {int(cd)}s cooldown active (Cloudflare-blocking our datacenter IP)"
            if cd > 0 else "AO3 reachable (no cooldown active)"
        ),
    }


@router.post("/admin/clear-ao3-cooldown")
def clear_ao3_cooldown_endpoint(
    _admin=Depends(require_admin),
):
    """Force-clear the AO3 cooldown so the next scrape will retry immediately.
    Useful when you know AO3 has come back (e.g. you tested with curl from the
    server and got a 200), but our cooldown timer hasn't expired yet."""
    from live_fetch.ao3_feeds import clear_ao3_cooldown
    clear_ao3_cooldown()
    return {"ok": True, "message": "AO3 cooldown cleared. Next request will hit AO3 fresh."}


# ── Admin: remove orphaned example/seed stories ──────────────────────────────

@router.delete("/admin/cleanup-seeds")
def cleanup_seeds(dry_run: bool = False, db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    """Remove the fabricated demo stories written by seed_data.py.

    Those rows are invented works ("realistic test stories for UI development")
    carrying invented kudos counts in the tens of thousands — higher than anything
    else in the AO3 set — so they sorted to the top of real searches, and their
    URLs point at unrelated genuine AO3 works.

    The previous matcher looked for site_id LIKE 'seed%'/'example%'/'test%'/'demo%'
    and placeholder author names. seed_data.py uses neither: its rows have numeric
    site_ids (1234001-1234008) and real-looking author names, so the cleanup never
    matched the very data it exists to remove. Worse, `seed%` DID match the
    synthetic site_ids of the janelleshane metadata seed, so running this endpoint
    would have deleted that entire legitimate import.

    Match the demo rows by their actual signature instead, and never touch a row
    carrying a provenance tag from a real importer.
    """
    from sqlalchemy import or_, and_, not_

    # Provenance tags applied by genuine bulk importers. Rows carrying one of these
    # are real index data regardless of what their site_id looks like.
    PROVENANCE_TAGS = [
        "janelleshane_seed", "hpffa_archive", "hexfiles_archive",
        "squidgeworld_archive", "dlp_library",
    ]

    looks_like_demo = or_(
        # The tag seed_data.py now stamps on every fixture — matching by
        # provenance rather than guessing at a site_id pattern, which is what
        # made this endpoint miss the very rows it exists to remove.
        Story.tags.any("ui_fixture"),
        # Older fixtures predating that tag. Matched by EXACT id, not a range:
        # `^123400[0-9]$` also caught site_id 1234000, which is a genuine AO3
        # work ("Fortune Teller" by Margo_Kim) — this endpoint would have
        # deleted it.
        and_(Story.site == SiteEnum.ao3,
             Story.site_id.in_([f"123400{n}" for n in range(1, 9)])),
        Story.site_id.like("example%"),
        Story.site_id.like("test%"),
        Story.site_id.like("demo%"),
        Story.author.in_(["Example Author", "Test Author", "Demo Author", "Seed Author"]),
        Story.tags.any("example"),
    )

    candidates = db.query(Story).filter(
        looks_like_demo,
        # Story.tags is a generic ARRAY column, so it has no .overlap(); .any() is
        # the comparator this file uses elsewhere.
        not_(or_(*[Story.tags.any(t) for t in PROVENANCE_TAGS])),
        Story.is_hosted == False,  # noqa: E712 — never delete full text we hold
    ).all()

    removed = [{"id": str(s.id), "title": s.title, "site_id": s.site_id,
                "site": s.site.value if s.site else None, "kudos": s.kudos}
               for s in candidates]
    if not dry_run:
        for s in candidates:
            db.delete(s)
        db.commit()
    return {"ok": True, "dry_run": dry_run,
            "removed_count": len(removed), "removed": removed}


@router.post("/cleanup-preface-chapters")
def cleanup_preface_chapters(dry_run: bool = Form(False), db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    """Fix already-imported stories whose first 'chapter' is actually FicHub/AO3
    front matter (the metadata sheet + author's preliminary notes) rather than a
    real chapter — the bug this addresses surfaced those as Chapter 1.

    We look at each hosted story's first chapter and flag it as front matter if it
    carries the AO3 metadata label cluster (Rating:/Fandom:/Stats: etc.) and is
    short. Flagged first chapters are removed and the remaining chapters renumbered.
    Pass dry_run=true to preview counts without changing anything.
    """
    from models.story import Story, Chapter

    meta_labels = ("rating:", "archive warning:", "warning:", "category:", "fandom:",
                   "relationship:", "character:", "additional tags:", "tags:", "stats:",
                   "published:", "completed:", "words:", "chapters:", "summary:")
    note_markers = ("a/n", "author's note", "authors note", "author note",
                    "please review", "please r&r", "updates every",
                    "disclaimer:", "i don't own", "i do not own")

    affected = []
    # Only stories with >1 chapter are safe to trim (never leave a story empty).
    stories = (db.query(Story)
               .filter(Story.is_hosted == True)  # noqa: E712
               .filter(Story.chapter_count > 1)
               .all())
    for s in stories:
        first = (db.query(Chapter)
                 .filter(Chapter.story_id == s.id)
                 .order_by(Chapter.number.asc())
                 .first())
        if not first or not first.content:
            continue
        text_only = re.sub(r"<[^>]+>", " ", first.content)
        words = len(text_only.split())
        low = text_only.lower()
        label_hits = sum(1 for lbl in meta_labels if lbl in low)
        note_hit = any(m in low for m in note_markers)
        looks_like_frontmatter = ((label_hits >= 4 and words < 600) or
                                  (label_hits >= 2 and words < 200) or
                                  (note_hit and words < 250))
        title_is_frontmatter = (first.title or "").strip().lower() in (
            "preface", "title page", "notes", "summary", "tags", "table of contents")
        if looks_like_frontmatter or title_is_frontmatter:
            affected.append({"id": str(s.id), "title": s.title,
                             "first_chapter_words": words, "label_hits": label_hits})
            if not dry_run:
                try:
                    removed_number = first.number
                    db.delete(first)
                    # Flush the delete FIRST so the old row leaves the
                    # (story_id, number) unique index before we reuse number 1.
                    db.flush()

                    rest = (db.query(Chapter)
                            .filter(Chapter.story_id == s.id,
                                    Chapter.number > removed_number)
                            .order_by(Chapter.number.asc())
                            .all())
                    # Two-phase renumber so an UPDATE never lands on a number
                    # another row still holds: first move everyone to unique
                    # negative temporaries, flush, then to the final values.
                    for ch in rest:
                        ch.number = -ch.number
                    db.flush()
                    for ch in rest:
                        ch.number = (-ch.number) - 1
                    s.chapter_count = max(1, (s.chapter_count or 1) - 1)
                    db.commit()
                except Exception:
                    db.rollback()
                    # Drop this one from the affected list since we didn't change it.
                    affected[-1]["error"] = "skipped (renumber conflict)"

    return {"ok": True, "dry_run": dry_run, "affected_count": len(affected),
            "affected": affected[:100]}
