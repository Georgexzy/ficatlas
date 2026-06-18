"""Search API — unified search across all indexed sites with hybrid live fetch"""
import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import Optional, List
from pydantic import BaseModel

from db.session import get_db
from models.story import Story, SiteEnum, RatingEnum, StatusEnum
from query_parser import parse_query, parsed_to_search_params

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class StoryCard(BaseModel):
    id: str
    site: str
    url: str
    title: str
    author: str
    author_url: Optional[str] = None
    summary: Optional[str] = None
    language: str
    rating: Optional[str] = None
    status: str
    word_count: int
    chapter_count: int
    chapter_count_total: Optional[int] = None
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
    published_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_live: bool = False          # true = came from live fetch, not index
    is_hosted: bool = False        # true = full text stored locally, one-click reader

    class Config:
        from_attributes = True


class ParsedToken(BaseModel):
    key: str
    value: str
    exclude: bool
    raw: str


class SearchResponse(BaseModel):
    total: int
    count_is_capped: bool = False  # True when total hit the count ceiling (show "5000+")
    page: int
    per_page: int
    results: List[StoryCard]
    sites_searched: List[str]
    live_count: int = 0            # how many results came from live fetch
    parsed_tokens: List[ParsedToken] = []  # for UI filter highlighting


SORT_MAP = {
    "relevance":       None,
    "updated_desc":    Story.updated_at.desc(),
    "updated_asc":     Story.updated_at.asc(),
    "published_desc":  Story.published_at.desc(),
    "kudos_desc":      Story.kudos.desc(),
    "hits_desc":       Story.hits.desc(),
    "word_count_desc": Story.word_count.desc(),
    "word_count_asc":  Story.word_count.asc(),
    "comments_desc":   Story.comments.desc(),
    "bookmarks_desc":  Story.bookmarks.desc(),
}


# ── Live fetch trigger logic ──────────────────────────────────────────────────

def _should_fetch_live(sort: str, page: int, q: Optional[str]) -> bool:
    """Only fetch live on page 1 for recency-biased sorts or when text query present."""
    if page > 1:
        return False
    return sort in ("updated_desc", "relevance") or bool(q)


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.get("", response_model=SearchResponse)
async def search(
    q:                     Optional[str] = Query(None),
    sites:                 Optional[str] = Query(None),
    fandoms:               Optional[str] = Query(None),
    characters:            Optional[str] = Query(None),
    relationships:         Optional[str] = Query(None),
    tags:                  Optional[str] = Query(None),
    ratings:               Optional[str] = Query(None),
    warnings:              Optional[str] = Query(None),
    categories:            Optional[str] = Query(None),
    crossovers:            Optional[str] = Query(None),
    exclude_fandoms:       Optional[str] = Query(None),
    exclude_characters:    Optional[str] = Query(None),
    exclude_relationships: Optional[str] = Query(None),
    exclude_tags:          Optional[str] = Query(None),
    exclude_ratings:       Optional[str] = Query(None),
    exclude_warnings:      Optional[str] = Query(None),
    exclude_categories:    Optional[str] = Query(None),
    status:                Optional[str] = Query(None),
    language:              Optional[str] = Query(None),
    word_count_min:        Optional[int] = Query(None, ge=0),
    word_count_max:        Optional[int] = Query(None),
    updated_after:         Optional[str] = Query(None),
    updated_before:        Optional[str] = Query(None),
    published_after:       Optional[str] = Query(None),
    explicit:              bool          = Query(False),
    search_within:         Optional[str] = Query(None),
    sort:                  str           = Query("relevance"),
    page:                  int           = Query(1, ge=1),
    per_page:              int           = Query(20, ge=1, le=100),
    live:                  bool          = Query(True, description="Enable hybrid live fetch"),
    db: Session = Depends(get_db),
):
    # ── Parse q for embedded operators ───────────────────────────────────────
    parsed_tokens = []
    if q:
        pq = parse_query(q)
        parsed_tokens = pq.tokens
        parsed_params = parsed_to_search_params(pq)

        # Merge parsed values into explicit params (explicit params win)
        if not fandoms       and parsed_params.get("fandoms"):       fandoms       = parsed_params["fandoms"]
        if not relationships and parsed_params.get("relationships"): relationships = parsed_params["relationships"]
        if not characters    and parsed_params.get("characters"):    characters    = parsed_params["characters"]
        if not tags          and parsed_params.get("tags"):          tags          = parsed_params["tags"]
        if not ratings       and parsed_params.get("ratings"):       ratings       = parsed_params["ratings"]
        if not status        and parsed_params.get("status"):        status        = parsed_params["status"]
        if not language      and parsed_params.get("language"):      language      = parsed_params["language"]
        if word_count_min is None and parsed_params.get("word_count_min"): word_count_min = parsed_params["word_count_min"]
        if word_count_max is None and parsed_params.get("word_count_max"): word_count_max = parsed_params["word_count_max"]
        if not updated_after and parsed_params.get("updated_after"): updated_after = parsed_params["updated_after"]
        if not crossovers    and parsed_params.get("crossovers"):    crossovers    = parsed_params["crossovers"]
        if not sites         and parsed_params.get("sites"):         sites         = parsed_params["sites"]
        if not exclude_fandoms       and parsed_params.get("exclude_fandoms"):       exclude_fandoms       = parsed_params["exclude_fandoms"]
        if not exclude_relationships and parsed_params.get("exclude_relationships"): exclude_relationships = parsed_params["exclude_relationships"]
        if not exclude_characters    and parsed_params.get("exclude_characters"):    exclude_characters    = parsed_params["exclude_characters"]
        if not exclude_tags          and parsed_params.get("exclude_tags"):          exclude_tags          = parsed_params["exclude_tags"]

        # Replace q with just the clean free text
        q = pq.clean_text or None

    # ── Site list ─────────────────────────────────────────────────────────────
    active_sites = [s.strip() for s in sites.split(",")] if sites else ["ao3", "ffnet"]
    site_enums   = [SiteEnum(s) for s in active_sites if s in SiteEnum.__members__]

    # ── Build DB query ────────────────────────────────────────────────────────
    db_query = db.query(Story)
    filters  = []

    if site_enums:
        filters.append(Story.site.in_(site_enums))

    if not explicit:
        filters.append(Story.rating != RatingEnum.explicit)

    if q:
        terms = q.strip().split()
        for term in terms:
            filters.append(or_(
                Story.title.ilike(f"%{term}%"),
                Story.summary.ilike(f"%{term}%"),
                Story.author.ilike(f"%{term}%"),
                func.array_to_string(Story.fandoms, ",").ilike(f"%{term}%"),
                func.array_to_string(Story.characters, ",").ilike(f"%{term}%"),
                func.array_to_string(Story.relationships, ",").ilike(f"%{term}%"),
                func.array_to_string(Story.tags, ",").ilike(f"%{term}%"),
            ))

    if search_within:
        filters.append(or_(
            Story.title.ilike(f"%{search_within}%"),
            Story.summary.ilike(f"%{search_within}%"),
        ))

    def arr_inc(col, csv_val, permissive_empty=True):
        """Array-includes filter.

        permissive_empty=True (default, for secondary facets like characters/ships/
        tags): a story matches if it contains ALL requested values OR has no data for
        that field. Missing secondary metadata shouldn't exclude a story.

        permissive_empty=False (for the FANDOM axis): empty arrays do NOT match. A
        story with no fandom listed should never surface under a specific-fandom
        search, otherwise the millions of empty-fandom dump rows leak into every
        fandom query and wildly inflate the count.
        """
        if not csv_val: return None
        vals = [v.strip().lower() for v in csv_val.split(",") if v.strip()]
        if not vals: return None
        if permissive_empty:
            empty = or_(col.is_(None), func.cardinality(col) == 0)
            return and_(*[
                or_(func.array_to_string(col, ",").ilike(f"%{v}%"), empty)
                for v in vals
            ])
        return and_(*[
            func.array_to_string(col, ",").ilike(f"%{v}%")
            for v in vals
        ])

    def arr_exc(col, csv_val):
        """Strict exclude: only kick out stories that DEFINITELY have the unwanted
        value. Empty arrays pass — we can't confirm presence of what we're excluding.
        """
        if not csv_val: return None
        vals = [v.strip().lower() for v in csv_val.split(",") if v.strip()]
        return and_(*[~func.array_to_string(col, ",").ilike(f"%{v}%") for v in vals]) if vals else None

    # Fandom is the primary axis → strict. Everything else → permissive on empty.
    f = arr_inc(Story.fandoms, fandoms, permissive_empty=False)
    if f is not None: filters.append(f)

    for col, val in [
        (Story.characters, characters),
        (Story.relationships, relationships), (Story.tags, tags),
        (Story.warnings, warnings), (Story.categories, categories),
    ]:
        f = arr_inc(col, val)
        if f is not None: filters.append(f)

    for col, val in [
        (Story.fandoms, exclude_fandoms), (Story.characters, exclude_characters),
        (Story.relationships, exclude_relationships), (Story.tags, exclude_tags),
        (Story.warnings, exclude_warnings), (Story.categories, exclude_categories),
    ]:
        f = arr_exc(col, val)
        if f is not None: filters.append(f)

    if ratings:
        r_vals = [r.strip().upper() for r in ratings.split(",")]
        valid  = [RatingEnum(r) for r in r_vals if r in RatingEnum._value2member_map_]
        # Permissive: stories with NULL rating still pass (HF FFN dump rows etc)
        if valid: filters.append(or_(Story.rating.in_(valid), Story.rating.is_(None)))

    if exclude_ratings:
        r_vals = [r.strip().upper() for r in exclude_ratings.split(",")]
        valid  = [RatingEnum(r) for r in r_vals if r in RatingEnum._value2member_map_]
        # Stories with NULL rating pass an exclude (we can't confirm they're rated)
        if valid: filters.append(or_(Story.rating.notin_(valid), Story.rating.is_(None)))

    if crossovers == "only":    filters.append(Story.is_crossover == True)
    elif crossovers == "exclude": filters.append(Story.is_crossover == False)

    if status:
        s_vals = [StatusEnum(s.strip()) for s in status.split(",") if s.strip() in StatusEnum.__members__]
        if s_vals:
            # Permissive: a story with NULL status (e.g. from HF FFN dump) still passes —
            # we can't prove it doesn't match the user's filter, so don't exclude it.
            filters.append(or_(Story.status.in_(s_vals), Story.status.is_(None)))

    if language:
        filters.append(or_(Story.language.ilike(language), Story.language.is_(None)))
    if word_count_min:
        # Permissive on NULL (unknown metadata) but NOT on 0 — a literal 0-word
        # story is fanart/art/placeholder and must be excluded by a min-words filter.
        filters.append(or_(Story.word_count >= word_count_min, Story.word_count.is_(None)))
    if word_count_max:
        filters.append(or_(Story.word_count <= word_count_max, Story.word_count.is_(None)))
    if updated_after:
        filters.append(or_(Story.updated_at >= updated_after, Story.updated_at.is_(None)))
    if updated_before:
        filters.append(or_(Story.updated_at <= updated_before, Story.updated_at.is_(None)))
    if published_after:
        filters.append(or_(Story.published_at >= published_after, Story.published_at.is_(None)))

    if filters:
        db_query = db_query.filter(and_(*filters))

    # Counting all matching rows on a multi-million table is the slowest part of
    # search. We only need an exact count up to a ceiling; beyond that "5000+" is
    # fine for pagination UI. This caps the count subquery for big result sets.
    COUNT_CEILING = 5000
    count_subq = db_query.order_by(None).limit(COUNT_CEILING + 1).subquery()
    total = db.query(func.count()).select_from(count_subq).scalar() or 0
    count_is_capped = total > COUNT_CEILING

    sort_expr = SORT_MAP.get(sort)
    db_query  = db_query.order_by(sort_expr if sort_expr is not None else Story.kudos.desc())

    offset  = (page - 1) * per_page
    indexed = db_query.offset(offset).limit(per_page).all()
    indexed_cards = [_to_card(s) for s in indexed]

    # ── Hybrid live fetch (page 1 only) ──────────────────────────────────────
    live_cards: list[StoryCard] = []
    if live and _should_fetch_live(sort, page, q):
        live_params = {
            "q": q, "fandoms": fandoms, "relationships": relationships,
            "characters": characters, "tags": tags, "ratings": ratings,
            "status": status, "word_count_min": word_count_min,
            "word_count_max": word_count_max, "sort": sort,
            "explicit": explicit,
        }
        try:
            live_tasks = []
            if "ao3" in active_sites:
                from live_fetch.ao3_live import fetch_live_ao3
                # Fetch 3 pages = up to ~60 fresh results per search
                live_tasks.append(fetch_live_ao3(live_params, limit=60, pages=3))

            if live_tasks:
                raw_results = await asyncio.gather(*live_tasks, return_exceptions=True)
                indexed_urls = {c.url for c in indexed_cards}
                live_raw_for_persist: list[dict] = []
                for batch in raw_results:
                    if isinstance(batch, Exception):
                        continue
                    for item in batch:
                        live_raw_for_persist.append(item)
                        if item["url"] not in indexed_urls:
                            live_cards.append(_dict_to_card(item))
                            indexed_urls.add(item["url"])

                # Persist live results into the DB so the index grows organically
                try:
                    from live_fetch.persist import persist_live_results
                    persist_live_results(db, live_raw_for_persist)
                except Exception:
                    pass  # persistence failure shouldn't break search
        except Exception:
            pass  # live fetch failure is non-fatal

    # Merge: live results (fresher) shown first on page 1, then the full indexed page.
    # We do NOT truncate indexed cards — that was dropping indexed results off page 1
    # so they never reappeared on page 2. Live cards are extra discovery on top of
    # page 1 only; pagination through `total` is driven purely by the indexed count.
    merged = live_cards + indexed_cards

    return SearchResponse(
        total=total,                          # stable across pages — indexed count only
        count_is_capped=count_is_capped,
        page=page,
        per_page=per_page,
        results=merged,
        sites_searched=[s.value for s in site_enums],
        live_count=len(live_cards),
        parsed_tokens=[ParsedToken(**t) for t in parsed_tokens],
    )


@router.get("/random", response_model=List[StoryCard])
async def random_stories(
    count: int = Query(3, ge=1, le=12),
    fandom: Optional[str] = Query(None),
    min_words: Optional[int] = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    """Surprise-me discovery: returns N random stories, optionally constrained by
    fandom and a minimum word count (so you don't get art/drabbles). Uses
    TABLESAMPLE-style random ordering. Biased toward stories with real metadata
    (non-zero word count) so results are actually readable."""
    q = db.query(Story).filter(Story.word_count > (min_words or 1000))
    if fandom:
        q = q.filter(func.array_to_string(Story.fandoms, ",").ilike(f"%{fandom.strip()}%"))
    # ORDER BY random() is fine at this scale for a handful of rows.
    rows = q.order_by(func.random()).limit(count).all()
    return [_to_card(s) for s in rows]


# ── Serialisers ───────────────────────────────────────────────────────────────

def _to_card(s: Story) -> StoryCard:
    return StoryCard(
        id=str(s.id), site=s.site.value, url=s.url,
        title=s.title, author=s.author or "Anonymous", author_url=s.author_url,
        summary=s.summary, language=s.language or "English",
        rating=s.rating.value if s.rating else None,
        status=s.status.value if s.status else "unknown",
        word_count=s.word_count or 0, chapter_count=s.chapter_count or 1,
        chapter_count_total=s.chapter_count_total,
        kudos=s.kudos or 0, hits=s.hits or 0,
        bookmarks=s.bookmarks or 0, comments=s.comments or 0,
        fandoms=s.fandoms or [], relationships=s.relationships or [],
        characters=s.characters or [], tags=s.tags or [],
        warnings=s.warnings or [], categories=s.categories or [],
        genres=s.genres or [],
        published_at=s.published_at.isoformat() if s.published_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
        is_live=False,
        is_hosted=bool(s.is_hosted),
    )


def _dict_to_card(d: dict) -> StoryCard:
    return StoryCard(
        id=d.get("id", ""), site=d.get("site", ""), url=d.get("url", ""),
        title=d.get("title", "Untitled"), author=d.get("author", "Anonymous"),
        author_url=d.get("author_url"), summary=d.get("summary"),
        language=d.get("language", "English"), rating=d.get("rating"),
        status=d.get("status", "unknown"),
        word_count=d.get("word_count", 0), chapter_count=d.get("chapter_count", 1),
        chapter_count_total=d.get("chapter_count_total"),
        kudos=d.get("kudos", 0), hits=d.get("hits", 0),
        bookmarks=d.get("bookmarks", 0), comments=d.get("comments", 0),
        fandoms=d.get("fandoms", []), relationships=d.get("relationships", []),
        characters=d.get("characters", []), tags=d.get("tags", []),
        warnings=d.get("warnings", []), categories=d.get("categories", []),
        genres=d.get("genres", []),
        published_at=d.get("published_at"), updated_at=d.get("updated_at"),
        is_live=d.get("is_live", True),
    )
