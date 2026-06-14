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

    # Parse <item> tags — attributes can be in ANY order, so extract id and href separately
    items = {}
    for item_tag in re.findall(r'<item\b[^>]*>', opf):
        id_m   = re.search(r'\bid="([^"]+)"', item_tag)
        href_m = re.search(r'\bhref="([^"]+)"', item_tag)
        if id_m and href_m:
            items[id_m.group(1)] = href_m.group(1)

    base_dir = os.path.dirname(opf_path)
    chapters = []

    # Build the ordered list of hrefs to read
    ordered_hrefs = []
    if spine_ids and items:
        for sid in spine_ids:
            if sid in items:
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

        # Extract body content
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        body = body_match.group(1) if body_match else html

        # Pull chapter title
        title_match = re.search(r"<h\d[^>]*>(.*?)</h\d>", body, re.DOTALL)
        ch_title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else None

        # Strip scripts/styles
        body = re.sub(r"<script.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)

        # Word count
        text_only = re.sub(r"<[^>]+>", " ", body)
        words = len(text_only.split())

        # Skip near-empty files (covers, nav pages)
        if words < 10:
            continue

        chapters.append({
            "number": len(chapters) + 1, "title": ch_title, "content": body, "word_count": words,
        })

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
async def upload_epub(file: UploadFile = File(...), db: Session = Depends(get_db)):
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
async def upload_epubs(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
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


@router.post("/import-url")
async def import_url(url: str = Form(...), db: Session = Depends(get_db)):
    """Fetch a story from AO3/FFnet via FicHub and import it as a hosted story."""
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


@router.get("/hosted")
async def list_hosted(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    """List all stories hosted on FicAtlas (EPUB uploads + URL imports), newest first."""
    q = (db.query(Story)
         .filter(Story.is_hosted == True)
         .order_by(Story.indexed_at.desc())
         .offset(offset).limit(min(limit, 200)))
    rows = q.all()
    return [
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


# ── AO3 Atom feed discovery (the reliable fresh-data path) ───────────────────

@router.post("/poll-feed")
async def poll_feed(
    fandom: str = Form(...),
    min_words: Optional[int] = Form(None),
    max_words: Optional[int] = Form(None),
    complete_only: bool = Form(False),
    db: Session = Depends(get_db),
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
async def autopoll(db: Session = Depends(get_db)):
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
async def delete_hosted(story_id: str, db: Session = Depends(get_db)):
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
            _ingest_epub_from_url(db, u["url"], epub_bytes, SiteEnum.ffnet)
            db.commit()
            imported += 1
        except Exception as e:
            db.rollback()
            log.warning(f"Auto-import {u['url']} failed: {e}")
            failed += 1

    return {"ok": True, "found": len(urls), "urls": urls, "imported": imported, "failed": failed}


def _ingest_epub_from_url(db: Session, url: str, epub_bytes: bytes, site: SiteEnum) -> dict:
    """Helper: parse epub bytes and persist as a hosted story for a given URL."""
    parsed = parse_epub(epub_bytes)
    existing = db.query(Story).filter(Story.url == url).first()
    if existing:
        existing.is_hosted = True
        db.query(Chapter).filter(Chapter.story_id == existing.id).delete()
        story = existing
    else:
        site_id = url.rstrip("/").split("/")[-1]
        story = Story(
            id=uuid.uuid4(), site=site, site_id=f"import_{site_id}", url=url,
            title=parsed["title"] or "Imported", author=parsed["author"] or "Unknown",
            summary=parsed["summary"], language=parsed["language"],
            rating=RatingEnum.not_rated, status=StatusEnum.complete,
            word_count=parsed["word_count"], chapter_count=len(parsed["chapters"]),
            chapter_count_total=len(parsed["chapters"]),
            fandoms=[], characters=[], relationships=[],
            tags=["imported", "via_wayback"],
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

@router.post("/discover-dlp")
async def discover_dlp(
    corpus: str = Form("hp"),            # "hp" or "other"
    limit: int = Form(200),
    auto_import: bool = Form(False),
    prefer: str = Form("ao3"),           # which URL to import: "ao3" or "ffn"
    db: Session = Depends(get_db),
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

    if not auto_import:
        return {"ok": True, "found": len(entries), "entries": entries, "imported": 0}

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
            result = _ingest_epub_from_url(db, chosen_url, epub_bytes, site)

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
