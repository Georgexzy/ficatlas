"""Search API — unified search across all indexed sites"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, cast, Integer, func, text
from typing import Optional, List
from pydantic import BaseModel
from enum import Enum

from db.session import get_db
from models.story import Story, SiteEnum, RatingEnum, StatusEnum

router = APIRouter()


# ── Response schemas ────────────────────────────────────────────────────────

class StoryCard(BaseModel):
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

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    total: int
    page: int
    per_page: int
    results: List[StoryCard]
    sites_searched: List[str]


# ── Sort options ─────────────────────────────────────────────────────────────

SORT_MAP = {
    "relevance":    None,
    "updated_desc": Story.updated_at.desc(),
    "updated_asc":  Story.updated_at.asc(),
    "published_desc": Story.published_at.desc(),
    "kudos_desc":   Story.kudos.desc(),
    "hits_desc":    Story.hits.desc(),
    "word_count_desc": Story.word_count.desc(),
    "word_count_asc":  Story.word_count.asc(),
    "comments_desc":   Story.comments.desc(),
    "bookmarks_desc":  Story.bookmarks.desc(),
}


# ── Main search endpoint ─────────────────────────────────────────────────────

@router.get("", response_model=SearchResponse)
async def search(
    # Free text
    q: Optional[str] = Query(None, description="Full-text query"),

    # Sites
    sites: Optional[str] = Query(None, description="Comma-separated: ao3,ffnet,wattpad"),

    # ── INCLUDE filters (AO3-parity) ──────────────────────────────────────
    fandoms:        Optional[str] = Query(None, description="Include fandoms (comma-separated)"),
    characters:     Optional[str] = Query(None, description="Include characters"),
    relationships:  Optional[str] = Query(None, description="Include relationships/pairings"),
    tags:           Optional[str] = Query(None, description="Include additional tags"),
    ratings:        Optional[str] = Query(None, description="Include ratings: G,T,M,E,NR"),
    warnings:       Optional[str] = Query(None, description="Include archive warnings"),
    categories:     Optional[str] = Query(None, description="Include categories: F/F,F/M,M/M,Gen"),
    crossovers:     Optional[str] = Query(None, description="include|exclude|only"),

    # ── EXCLUDE filters ───────────────────────────────────────────────────
    exclude_fandoms:       Optional[str] = Query(None),
    exclude_characters:    Optional[str] = Query(None),
    exclude_relationships: Optional[str] = Query(None),
    exclude_tags:          Optional[str] = Query(None),
    exclude_ratings:       Optional[str] = Query(None),
    exclude_warnings:      Optional[str] = Query(None),
    exclude_categories:    Optional[str] = Query(None),

    # ── More options ──────────────────────────────────────────────────────
    status:         Optional[str] = Query(None, description="complete|in_progress|abandoned"),
    language:       Optional[str] = Query(None),
    word_count_min: Optional[int] = Query(None, ge=0),
    word_count_max: Optional[int] = Query(None),
    updated_after:  Optional[str] = Query(None, description="ISO date e.g. 2024-01-01"),
    updated_before: Optional[str] = Query(None),
    published_after: Optional[str] = Query(None),
    explicit:       bool = Query(False, description="Include explicit content"),

    # Search within results
    search_within:  Optional[str] = Query(None, description="Narrow within current results"),

    # ── Pagination & sort ─────────────────────────────────────────────────
    sort:    str = Query("relevance", description="Sort field"),
    page:    int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),

    db: Session = Depends(get_db),
):
    query = db.query(Story)
    filters = []

    # ── Site filter ───────────────────────────────────────────────────────
    active_sites = [s.strip() for s in sites.split(",")] if sites else ["ao3", "ffnet"]
    site_enums = [SiteEnum(s) for s in active_sites if s in SiteEnum.__members__]
    if site_enums:
        filters.append(Story.site.in_(site_enums))

    # ── Explicit filter ───────────────────────────────────────────────────
    if not explicit:
        filters.append(Story.rating != RatingEnum.explicit)

    # ── Full-text search ──────────────────────────────────────────────────
    if q:
        # Use PostgreSQL to_tsquery for real installations
        # Fallback: ILIKE on title + summary for dev
        terms = q.strip().split()
        text_filters = []
        for term in terms:
            text_filters.append(
                or_(
                    Story.title.ilike(f"%{term}%"),
                    Story.summary.ilike(f"%{term}%"),
                )
            )
        if text_filters:
            filters.append(and_(*text_filters))

    if search_within:
        filters.append(
            or_(
                Story.title.ilike(f"%{search_within}%"),
                Story.summary.ilike(f"%{search_within}%"),
            )
        )

    # ── Include array filters ─────────────────────────────────────────────
    def array_includes(column, csv: Optional[str]):
        if not csv:
            return None
        vals = [v.strip().lower() for v in csv.split(",") if v.strip()]
        if not vals:
            return None
        return and_(*[func.lower(column.cast(text)).contains(v) for v in vals])
        # Proper GIN overlap for real Postgres:
        # return column.overlap(cast(vals, ARRAY(Text)))

    def array_excludes(column, csv: Optional[str]):
        if not csv:
            return None
        vals = [v.strip().lower() for v in csv.split(",") if v.strip()]
        if not vals:
            return None
        return and_(*[~func.lower(column.cast(text)).contains(v) for v in vals])

    # Include
    for col, val in [
        (Story.fandoms, fandoms),
        (Story.characters, characters),
        (Story.relationships, relationships),
        (Story.tags, tags),
        (Story.warnings, warnings),
        (Story.categories, categories),
    ]:
        f = array_includes(col, val)
        if f is not None:
            filters.append(f)

    # Exclude
    for col, val in [
        (Story.fandoms, exclude_fandoms),
        (Story.characters, exclude_characters),
        (Story.relationships, exclude_relationships),
        (Story.tags, exclude_tags),
        (Story.warnings, exclude_warnings),
        (Story.categories, exclude_categories),
    ]:
        f = array_excludes(col, val)
        if f is not None:
            filters.append(f)

    # Ratings include/exclude
    if ratings:
        r_vals = [r.strip().upper() for r in ratings.split(",")]
        valid = [RatingEnum(r) for r in r_vals if r in RatingEnum.__members__.values()]
        if valid:
            filters.append(Story.rating.in_(valid))

    if exclude_ratings:
        r_vals = [r.strip().upper() for r in exclude_ratings.split(",")]
        valid = [RatingEnum(r) for r in r_vals if r in RatingEnum.__members__.values()]
        if valid:
            filters.append(Story.rating.notin_(valid))

    # Crossovers
    if crossovers == "only":
        filters.append(Story.is_crossover == True)
    elif crossovers == "exclude":
        filters.append(Story.is_crossover == False)

    # ── More options ──────────────────────────────────────────────────────
    if status:
        status_enums = [StatusEnum(s.strip()) for s in status.split(",") if s.strip() in StatusEnum.__members__]
        if status_enums:
            filters.append(Story.status.in_(status_enums))

    if language:
        filters.append(Story.language.ilike(language))

    if word_count_min is not None:
        filters.append(Story.word_count >= word_count_min)
    if word_count_max is not None:
        filters.append(Story.word_count <= word_count_max)

    if updated_after:
        filters.append(Story.updated_at >= updated_after)
    if updated_before:
        filters.append(Story.updated_at <= updated_before)
    if published_after:
        filters.append(Story.published_at >= published_after)

    # ── Apply filters & sort ──────────────────────────────────────────────
    if filters:
        query = query.filter(and_(*filters))

    total = query.count()

    sort_expr = SORT_MAP.get(sort)
    if sort_expr is not None:
        query = query.order_by(sort_expr)
    else:
        # Relevance: basic scoring by kudos when no text query
        query = query.order_by(Story.kudos.desc())

    offset = (page - 1) * per_page
    stories = query.offset(offset).limit(per_page).all()

    results = [_to_card(s) for s in stories]

    return SearchResponse(
        total=total,
        page=page,
        per_page=per_page,
        results=results,
        sites_searched=[s.value for s in site_enums],
    )


def _to_card(s: Story) -> StoryCard:
    return StoryCard(
        id=str(s.id),
        site=s.site.value,
        url=s.url,
        title=s.title,
        author=s.author or "Anonymous",
        author_url=s.author_url,
        summary=s.summary,
        language=s.language or "English",
        rating=s.rating.value if s.rating else None,
        status=s.status.value if s.status else "unknown",
        word_count=s.word_count or 0,
        chapter_count=s.chapter_count or 1,
        chapter_count_total=s.chapter_count_total,
        kudos=s.kudos or 0,
        hits=s.hits or 0,
        bookmarks=s.bookmarks or 0,
        comments=s.comments or 0,
        fandoms=s.fandoms or [],
        relationships=s.relationships or [],
        characters=s.characters or [],
        tags=s.tags or [],
        warnings=s.warnings or [],
        categories=s.categories or [],
        genres=s.genres or [],
        published_at=s.published_at.isoformat() if s.published_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
    )
