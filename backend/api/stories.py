"""Story detail + chapter reading endpoints"""
import html
import io
import re
import zipfile
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Text, bindparam, text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from db.session import get_db
from models.story import Story, Chapter
from provenance import content_tags, source_labels

router = APIRouter()


class ChapterMeta(BaseModel):
    id: str
    number: int
    title: Optional[str]
    word_count: int
    posted_at: Optional[str]


class ChapterFull(BaseModel):
    id: str
    number: int
    title: Optional[str]
    summary: Optional[str]
    content: str
    word_count: int
    posted_at: Optional[str]
    start_note: Optional[str]
    end_note: Optional[str]


class StoryDetail(BaseModel):
    id: str
    site: str
    url: str
    title: str
    author: str
    author_url: Optional[str]
    summary: Optional[str]
    language: str
    rating: Optional[str]
    status: str
    word_count: int
    chapter_count: int
    chapter_count_total: Optional[int]
    kudos: int
    hits: int
    bookmarks: int
    comments: int
    fandoms: List[str]
    relationships: List[str]
    characters: List[str]
    tags: List[str]
    warnings: List[str]
    categories: List[str]
    genres: List[str]
    published_at: Optional[str]
    updated_at: Optional[str]
    is_hosted: bool
    wayback_url: Optional[str]
    cross_post_urls: List[str] = []
    sources: List[str] = []
    last_checked: Optional[str] = None   # when we last re-verified this record
    chapters: List[ChapterMeta]


@router.get("/{story_id}", response_model=StoryDetail)
async def get_story(story_id: str, db: Session = Depends(get_db)):
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")

    chapter_meta = [
        ChapterMeta(
            id=str(c.id),
            number=c.number,
            title=c.title,
            word_count=c.word_count or 0,
            posted_at=c.posted_at.isoformat() if c.posted_at else None,
        )
        for c in (story.chapters or [])
    ]

    return StoryDetail(
        id=str(story.id),
        site=story.site.value,
        url=story.url,
        title=story.title,
        author=story.author or "Anonymous",
        author_url=story.author_url,
        summary=story.summary,
        language=story.language or "English",
        rating=story.rating.value if story.rating else None,
        status=story.status.value if story.status else "unknown",
        word_count=story.word_count or 0,
        chapter_count=story.chapter_count or 1,
        chapter_count_total=story.chapter_count_total,
        kudos=story.kudos or 0,
        hits=story.hits or 0,
        bookmarks=story.bookmarks or 0,
        comments=story.comments or 0,
        fandoms=story.fandoms or [],
        relationships=story.relationships or [],
        characters=story.characters or [],
        tags=content_tags(story.tags),
        sources=source_labels(story.tags),
        last_checked=story.crawled_at.isoformat() if story.crawled_at else None,
        warnings=story.warnings or [],
        categories=story.categories or [],
        genres=story.genres or [],
        published_at=story.published_at.isoformat() if story.published_at else None,
        updated_at=story.updated_at.isoformat() if story.updated_at else None,
        is_hosted=story.is_hosted or False,
        wayback_url=story.wayback_url,
        cross_post_urls=story.cross_post_urls or [],
        chapters=chapter_meta,
    )


@router.get("/{story_id}/chapters/{number}", response_model=ChapterFull)
async def get_chapter(story_id: str, number: int, db: Session = Depends(get_db)):
    chapter = (db.query(Chapter)
               .filter(Chapter.story_id == story_id, Chapter.number == number)
               .first())
    if not chapter:
        raise HTTPException(404, "Chapter not found")

    return ChapterFull(
        id=str(chapter.id),
        number=chapter.number,
        title=chapter.title,
        summary=chapter.summary,
        content=chapter.content,
        word_count=chapter.word_count or 0,
        posted_at=chapter.posted_at.isoformat() if chapter.posted_at else None,
        start_note=chapter.start_note,
        end_note=chapter.end_note,
    )


# ── Recommendations ──────────────────────────────────────────────────────────

class SimilarCard(BaseModel):
    id: str
    title: str
    author: str
    word_count: int
    fandoms: List[str] = []
    relationships: List[str] = []
    tags: List[str] = []
    is_hosted: bool = False


@router.get("/{story_id}/similar", response_model=List[SimilarCard])
async def similar_stories(
    story_id: str,
    count: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """"If you like this, try…" — stories sharing this one's tags.

    Scored by weighted overlap: relationships count most (a shared ship is the
    strongest signal of a similar read), then fandom, then freeform tags. Word
    count breaks ties, since it is the only quality-ish signal populated across
    the whole index — kudos is 0 for ~99.99% of rows.

    The candidate set is drawn via the GIN array indexes (&&) and capped before
    scoring, so this stays cheap on a multi-million row table instead of scoring
    everything that shares a common tag like "Fluff".
    """
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")

    ships = [s for s in (story.relationships or []) if s][:6]
    fandoms = [f for f in (story.fandoms or []) if f][:4]
    tags = [t for t in (story.tags or []) if t][:8]
    if not (ships or fandoms or tags):
        return []

    # Score in SQL over a bounded candidate set. cardinality(&&) gives the size of
    # the overlap per facet; the weights encode ship > fandom > tag.
    rows = db.execute(sql_text("""
        WITH candidates AS (
            SELECT id, title, author, word_count, fandoms, relationships, tags, is_hosted
            FROM stories
            WHERE id <> :sid
              AND (
                    (:has_ships   AND relationships && :ships)
                 OR (:has_fandoms AND fandoms       && :fandoms)
                 OR (:has_tags    AND tags          && :tags)
              )
            LIMIT 4000
        )
        SELECT id, title, author, word_count, fandoms, relationships, tags, is_hosted,
               ( 5 * cardinality(ARRAY(SELECT unnest(relationships) INTERSECT SELECT unnest(:ships)))
               + 3 * cardinality(ARRAY(SELECT unnest(fandoms)       INTERSECT SELECT unnest(:fandoms)))
               + 1 * cardinality(ARRAY(SELECT unnest(tags)          INTERSECT SELECT unnest(:tags)))
               ) AS score
        FROM candidates
        ORDER BY score DESC, word_count DESC NULLS LAST
        LIMIT :lim
    """).bindparams(
        # The array params MUST be explicitly typed. When a story has no ships (or
        # no tags) the list is empty, Postgres cannot infer its type, and the query
        # dies with "function unnest(unknown) is not unique".
        bindparam("ships", value=ships, type_=PG_ARRAY(Text)),
        bindparam("fandoms", value=fandoms, type_=PG_ARRAY(Text)),
        bindparam("tags", value=tags, type_=PG_ARRAY(Text)),
        sid=story.id,
        has_ships=bool(ships), has_fandoms=bool(fandoms), has_tags=bool(tags),
        lim=count,
    )).mappings().all()

    return [
        SimilarCard(
            id=str(r["id"]), title=r["title"], author=r["author"] or "Anonymous",
            word_count=r["word_count"] or 0, fandoms=r["fandoms"] or [],
            relationships=r["relationships"] or [], tags=r["tags"] or [],
            is_hosted=bool(r["is_hosted"]),
        )
        for r in rows if (r["score"] or 0) > 0
    ]


# ── EPUB export ──────────────────────────────────────────────────────────────

def _xml_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


# Elements that carry no meaning in a book and that e-readers reject or ignore.
_DROP_TAGS = ("script", "style", "meta", "link", "head", "iframe", "form", "input")

# Control characters that are simply not representable in XML 1.0. Scraped and
# EPUB-imported chapters do contain them, and a single one makes the book
# unparseable, so they are removed rather than escaped.
_BAD_XML_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F￾￿]|[\uD800-\uDFFF]"
)


def _strip_bad_xml_chars(s: str) -> str:
    return _BAD_XML_CHARS.sub("", s)


def _clean_chapter_html(raw: str) -> str:
    """Normalise a scraped chapter body into well-formed XHTML.

    EPUB content documents are XML, so anything less than well-formed makes the
    whole book invalid — and real scraped chapters are full of unclosed <p>, bare
    <br>, stray entities and mismatched inline tags. Regex cannot fix that (an
    earlier attempt here produced EPUBs whose chapters failed to parse). Parse
    with lxml's forgiving HTML parser, which repairs the tree, then serialise
    back out as XML so every tag is balanced and every void element self-closed.
    """
    if not raw or not raw.strip():
        return "<p></p>"
    try:
        import lxml.html
        from lxml import etree

        frag = lxml.html.fragment_fromstring(_strip_bad_xml_chars(raw),
                                             create_parent="div")
        for tag in _DROP_TAGS:
            for el in frag.iter(tag):
                el.getparent().remove(el)

        for el in frag.iter():
            if not isinstance(el.tag, str):
                continue
            # Chapters pasted out of Word carry namespaced tags like <o:p>. lxml
            # keeps the prefix, and serialising it as XML yields "unbound prefix"
            # — an unopenable book. Keep the local name only.
            if ":" in el.tag:
                el.tag = el.tag.rsplit(":", 1)[-1] or "span"
            for attr in list(el.attrib):
                low = attr.lower()
                if ":" in attr:
                    del el.attrib[attr]           # same problem, attribute side
                elif low.startswith("on"):        # event handlers
                    del el.attrib[attr]
                elif low in ("href", "src") and \
                        el.attrib[attr].strip().lower().startswith("javascript:"):
                    del el.attrib[attr]

        out = etree.tostring(frag, method="xml", encoding="unicode")
        return out.strip() or "<p></p>"
    except Exception:
        # Last resort: ship the text with markup stripped rather than fail the
        # download or emit a broken book.
        stripped = re.sub(r"(?s)<[^>]+>", " ", raw)
        return f"<p>{_xml_escape(re.sub(r'[ \t]+', ' ', stripped)).strip()}</p>"


@router.get("/{story_id}/export.epub")
async def export_epub(story_id: str, db: Session = Depends(get_db)):
    """Build an EPUB 2 of a hosted story on the fly. Standard library only —
    an EPUB is a ZIP with a fixed set of XML files, so no dependency is needed."""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")

    chapters = (db.query(Chapter)
                .filter(Chapter.story_id == story.id)
                .order_by(Chapter.number.asc())
                .all())
    if not chapters:
        raise HTTPException(
            404, "This story's full text isn't stored locally, so it can't be exported."
        )

    title = story.title or "Untitled"
    author = story.author or "Anonymous"
    book_id = f"urn:uuid:{story.id}"
    lang = (story.language or "en")[:16]

    buf = io.BytesIO()
    # mimetype must be the first entry and STORED, not deflated, per the spec.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr("META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>')

        # Title page carries the metadata the reader would otherwise lose.
        meta_bits = []
        for label, vals in (("Fandom", story.fandoms), ("Relationships", story.relationships),
                            ("Characters", story.characters), ("Tags", story.tags)):
            if vals:
                meta_bits.append(f"<p><strong>{label}:</strong> "
                                 f"{_xml_escape(', '.join(vals[:20]))}</p>")
        if story.summary:
            meta_bits.append(f"<h2>Summary</h2><p>{_xml_escape(story.summary)}</p>")
        if story.url and not story.url.startswith("seed://"):
            meta_bits.append(f'<p><a href="{_xml_escape(story.url)}">'
                             f'{_xml_escape(story.url)}</a></p>')

        z.writestr("OEBPS/title.xhtml", _xhtml(title, (
            f"<h1>{_xml_escape(title)}</h1><h2>by {_xml_escape(author)}</h2>"
            + "".join(meta_bits)
        )))

        manifest, spine, navpoints = [], [], []
        manifest.append('<item id="title" href="title.xhtml" '
                        'media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="title"/>')
        navpoints.append(
            '<navPoint id="nav-title" playOrder="1"><navLabel><text>Title</text></navLabel>'
            '<content src="title.xhtml"/></navPoint>')

        for i, ch in enumerate(chapters, start=1):
            name = f"chap{i}.xhtml"
            ch_title = ch.title or f"Chapter {ch.number}"
            parts = [f"<h2>{_xml_escape(ch_title)}</h2>"]
            if ch.start_note:
                parts.append(f"<div><em>{_xml_escape(ch.start_note)}</em></div><hr/>")
            parts.append(_clean_chapter_html(ch.content))
            if ch.end_note:
                parts.append(f"<hr/><div><em>{_xml_escape(ch.end_note)}</em></div>")
            z.writestr(f"OEBPS/{name}", _xhtml(ch_title, "".join(parts)))

            manifest.append(f'<item id="c{i}" href="{name}" '
                            f'media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            navpoints.append(
                f'<navPoint id="nav{i}" playOrder="{i + 1}"><navLabel>'
                f'<text>{_xml_escape(ch_title)}</text></navLabel>'
                f'<content src="{name}"/></navPoint>')

        z.writestr("OEBPS/toc.ncx",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{_xml_escape(book_id)}"/></head>'
            f'<docTitle><text>{_xml_escape(title)}</text></docTitle>'
            f'<navMap>{"".join(navpoints)}</navMap></ncx>')

        z.writestr("OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:opf="http://www.idpf.org/2007/opf">'
            f'<dc:title>{_xml_escape(title)}</dc:title>'
            f'<dc:creator opf:role="aut">{_xml_escape(author)}</dc:creator>'
            f'<dc:language>{_xml_escape(lang)}</dc:language>'
            f'<dc:identifier id="bookid">{_xml_escape(book_id)}</dc:identifier>'
            + (f'<dc:description>{_xml_escape(story.summary)}</dc:description>'
               if story.summary else '')
            + ''.join(f'<dc:subject>{_xml_escape(t)}</dc:subject>'
                      for t in (story.tags or [])[:25])
            + '</metadata>'
            f'<manifest>{"".join(manifest)}'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '</manifest>'
            f'<spine toc="ncx">{"".join(spine)}</spine></package>')

    safe = re.sub(r'[^\w\s-]', "", title).strip()[:60] or "story"
    safe = re.sub(r"\s+", "_", safe)
    return Response(
        content=buf.getvalue(),
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}.epub"'},
    )


def _xhtml(title: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
            '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            f'<head><title>{_xml_escape(title)}</title></head>'
            f'<body>{body}</body></html>')
