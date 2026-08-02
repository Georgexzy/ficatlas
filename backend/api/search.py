"""Search API — unified search across all indexed sites with hybrid live fetch"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session, aliased
from sqlalchemy import and_, or_, func, literal_column, cast, Text, text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from typing import Optional, List
from pydantic import BaseModel

from db.session import get_db
from models.story import Story, SiteEnum, RatingEnum, StatusEnum
from query_parser import parse_query, parsed_to_search_params
from character_aliases import character_variants, relationship_variants

router = APIRouter()
log = logging.getLogger(__name__)


# ── Indexed matching helpers ─────────────────────────────────────────────────
# These MUST stay byte-for-byte equivalent to the index expressions created in
# init_db.py. Postgres only uses an expression index when the query expression
# matches the indexed one, so changing either side without the other silently
# drops search back to a full sequential scan over every row.

# 'english'::regconfig as a literal, matching how Postgres stores it in the index
_REGCONFIG = literal_column("'english'::regconfig")


def _story_tsv(entity=Story):
    """The indexed tsvector over title + summary + author + all facet arrays.
    Backed by ix_stories_doc_fts. `entity` may be an alias of Story so the same
    expression can be re-applied over a materialised candidate subquery."""
    return func.to_tsvector(
        _REGCONFIG,
        func.fic_doc(
            entity.title, entity.summary, entity.author,
            entity.fandoms, entity.characters, entity.relationships, entity.tags,
        ),
    )


def _arr_text(col):
    """IMMUTABLE array->text used by the trigram indexes on the facet columns.
    Backed by ix_stories_{fandoms,characters,relationships,tags}_trgm."""
    return func.fic_arr(col)


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
    cross_post_urls: List[str] = []  # same work on other sites (deduped result)

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


# (column name, descending). Stored as names rather than bound expressions so the
# same sort can be applied either to Story or to an aliased subquery of it.
SORT_MAP: dict[str, tuple[str, bool] | None] = {
    "relevance":       None,
    "updated_desc":    ("updated_at", True),
    "updated_asc":     ("updated_at", False),
    "published_desc":  ("published_at", True),
    "kudos_desc":      ("kudos", True),
    "hits_desc":       ("hits", True),
    "word_count_desc": ("word_count", True),
    "word_count_asc":  ("word_count", False),
    "comments_desc":   ("comments", True),
    "bookmarks_desc":  ("bookmarks", True),
}


def _sort_expr(entity, sort: str):
    spec = SORT_MAP.get(sort)
    if spec is None:
        return None
    col = getattr(entity, spec[0])
    return col.desc().nullslast() if spec[1] else col.asc().nullslast()


# ── Live fetch trigger logic ──────────────────────────────────────────────────

async def _fetch_and_persist_live(live_params: dict, want_ao3: bool) -> None:
    """Pull fresh AO3 results and add them to the index. Runs after the response
    has been sent, so its latency is invisible to the reader.

    Opens its own DB session: the request-scoped one from Depends(get_db) is
    already closed by the time a background task runs.
    """
    if not want_ao3:
        return
    try:
        from live_fetch.ao3_live import fetch_live_ao3
        from live_fetch.persist import persist_live_results
        from db.session import db_session

        results = await fetch_live_ao3(live_params, limit=60, pages=3)
        if not results:
            return
        with db_session() as db:
            persist_live_results(db, results)
        log.info("live AO3: indexed %d results for q=%r", len(results), live_params.get("q"))
    except Exception as e:
        # Non-fatal by design, but log it — this path was silently broken for a
        # long time precisely because failures were swallowed without a trace.
        log.warning("live AO3 fetch/persist failed: %s: %s", type(e).__name__, e)


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
    include_unknown:       bool          = Query(
        False,
        description="Also return stories that have NO data for a filtered field "
                    "(e.g. no relationships listed). Off by default so filters "
                    "actually filter.",
    ),
    author:                Optional[str] = Query(
        None,
        description="Exact author match (case-insensitive). Unlike free text, this "
                    "returns only that author's works — across every archive.",
    ),
    search_within:         Optional[str] = Query(None),
    sort:                  str           = Query("relevance"),
    page:                  int           = Query(1, ge=1),
    per_page:              int           = Query(20, ge=1, le=100),
    live:                  bool          = Query(True, description="Enable hybrid live fetch"),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
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
        # Hide only stories KNOWN to be explicit. `rating != 'E'` is NULL (not TRUE)
        # for NULL-rating rows in SQL, which silently dropped the entire NULL-rating
        # bulk import (HF FFN dump etc.) from every default search. Permit NULL.
        filters.append(or_(Story.rating != RatingEnum.explicit, Story.rating.is_(None)))

    if q:
        # Free text goes through Postgres full-text search against the GIN index
        # (ix_stories_doc_fts) covering title, summary, author and every facet array.
        #
        # This replaces a per-term OR of seven ILIKE '%term%' predicates. No index
        # can serve a leading-wildcard ILIKE, so that form degraded to a full
        # sequential scan of all ~2.3M rows on every keystroke — and it was slowest
        # for RARE terms, because the count ceiling never filled up early enough to
        # short-circuit the scan.
        #
        # websearch_to_tsquery ANDs the terms together, which preserves the previous
        # "every term must appear somewhere" semantics, and it never raises on
        # malformed input (unlike to_tsquery), so user text is safe to pass straight
        # through. It also gives quoted phrases, OR and -negation for free.
        filters.append(
            _story_tsv().op("@@")(func.websearch_to_tsquery(_REGCONFIG, q))
        )

    if author:
        # Exact, case-insensitive. Free-text search matches the author field too,
        # but also every summary that merely mentions the name ("TRADUCCIÓN del fic
        # de SilentAuror"), so it can't answer "show me everything this person
        # wrote". Backed by ix_stories_author_lower.
        #
        # This is something neither AO3 nor FF.net can do: an author's page on
        # either site shows only the works they posted there, while this spans
        # every archive in the index.
        filters.append(func.lower(Story.author) == author.strip().lower())

    if search_within:
        filters.append(or_(
            Story.title.ilike(f"%{search_within}%"),
            Story.summary.ilike(f"%{search_within}%"),
        ))

    def arr_inc(col, csv_val, permissive_empty=False):
        """Array-includes filter: a story matches when the column contains ALL of
        the requested values.

        permissive_empty=True additionally lets through stories that have NO data
        for the column at all. That used to be the default for every secondary
        facet, on the reasoning that missing metadata shouldn't exclude a story.
        In practice it made those filters do nothing: 99.7% of indexed rows have no
        relationships and 98.8% have no characters, so filtering by a ship returned
        millions of stories that had no ship at all — swamping the handful that
        genuinely matched.

        Filters are now strict by default, and the caller opts into the permissive
        behaviour with include_unknown.
        """
        if not csv_val: return None
        vals = [v.strip().lower() for v in csv_val.split(",") if v.strip()]
        if not vals: return None
        if permissive_empty:
            empty = or_(col.is_(None), func.cardinality(col) == 0)
            return and_(*[
                or_(_arr_text(col).ilike(f"%{v}%"), empty)
                for v in vals
            ])
        return and_(*[
            _arr_text(col).ilike(f"%{v}%")
            for v in vals
        ])

    def arr_inc_aliased(col, csv_val, expand, permissive_empty=False):
        """Array-includes filter with alias expansion.

        For each requested value we look up every spelling the archives actually
        store. Those are matched as WHOLE array elements via the && (overlap)
        operator — critical because aliases like "H" and "D" are single letters
        that a substring match would find inside almost every row. Overlap also
        lets Postgres use the plain GIN array indexes.

        Names we have no aliases for fall back to the substring behaviour, so
        fandoms outside the alias table keep working exactly as before.
        """
        if not csv_val: return None
        vals = [v.strip() for v in csv_val.split(",") if v.strip()]
        if not vals: return None

        empty = or_(col.is_(None), func.cardinality(col) == 0)
        clauses = []
        for v in vals:
            variants = expand(v)
            if variants:
                match = col.op("&&")(cast(variants, PG_ARRAY(Text)))
            else:
                match = _arr_text(col).ilike(f"%{v.lower()}%")
            clauses.append(or_(match, empty) if permissive_empty else match)
        return and_(*clauses)

    def arr_exc(col, csv_val):
        """Strict exclude: only kick out stories that DEFINITELY have the unwanted
        value. Empty arrays pass — we can't confirm presence of what we're excluding.
        """
        if not csv_val: return None
        vals = [v.strip().lower() for v in csv_val.split(",") if v.strip()]
        return and_(*[~_arr_text(col).ilike(f"%{v}%") for v in vals]) if vals else None

    # Fandom is always strict — a story with no fandom listed must never surface
    # under a specific-fandom search, or the millions of empty-fandom dump rows
    # leak into every fandom query.
    f = arr_inc(Story.fandoms, fandoms, permissive_empty=False)
    if f is not None: filters.append(f)

    # Characters and relationships get alias expansion so a filter written the
    # natural way ("Draco/Hermione") also matches how each archive actually stores
    # it ("D/Hr", "Hermione Granger/Draco Malfoy"). See character_aliases.py.
    f = arr_inc_aliased(Story.characters, characters, character_variants,
                        permissive_empty=include_unknown)
    if f is not None: filters.append(f)

    f = arr_inc_aliased(Story.relationships, relationships, relationship_variants,
                        permissive_empty=include_unknown)
    if f is not None: filters.append(f)

    for col, val in [
        (Story.tags, tags),
        (Story.warnings, warnings), (Story.categories, categories),
    ]:
        f = arr_inc(col, val, permissive_empty=include_unknown)
        if f is not None: filters.append(f)

    for col, val in [
        (Story.fandoms, exclude_fandoms), (Story.characters, exclude_characters),
        (Story.relationships, exclude_relationships), (Story.tags, exclude_tags),
        (Story.warnings, exclude_warnings), (Story.categories, exclude_categories),
    ]:
        f = arr_exc(col, val)
        if f is not None: filters.append(f)

    # Scalar filters follow the same rule as the facet filters: a story only matches
    # if it actually carries the value. `include_unknown` re-admits rows where the
    # field is NULL (metadata we never captured), rather than that being the default.
    def _or_unknown(cond, *unknown_conds):
        return or_(cond, *unknown_conds) if include_unknown else cond

    if ratings:
        r_vals = [r.strip().upper() for r in ratings.split(",")]
        valid  = [RatingEnum(r) for r in r_vals if r in RatingEnum._value2member_map_]
        if valid:
            filters.append(_or_unknown(Story.rating.in_(valid), Story.rating.is_(None)))

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
            # "unknown" is a real stored value for bulk imports that carried no
            # completion data, so it counts as unknown here alongside NULL.
            filters.append(_or_unknown(Story.status.in_(s_vals),
                                       Story.status.is_(None),
                                       Story.status == StatusEnum.unknown))

    if language:
        filters.append(_or_unknown(Story.language.ilike(language), Story.language.is_(None)))
    if word_count_min:
        # NULL is unknown metadata, but a literal 0-word story is art/placeholder
        # and must always be excluded by a min-words filter.
        filters.append(_or_unknown(Story.word_count >= word_count_min, Story.word_count.is_(None)))
    if word_count_max:
        filters.append(_or_unknown(Story.word_count <= word_count_max, Story.word_count.is_(None)))
    if updated_after:
        filters.append(_or_unknown(Story.updated_at >= updated_after, Story.updated_at.is_(None)))
    if updated_before:
        filters.append(_or_unknown(Story.updated_at <= updated_before, Story.updated_at.is_(None)))
    if published_after:
        filters.append(_or_unknown(Story.published_at >= published_after, Story.published_at.is_(None)))

    if filters:
        db_query = db_query.filter(and_(*filters))

    # Counting all matching rows on a multi-million table is the slowest part of
    # search. We only need an exact count up to a ceiling; beyond that "5000+" is
    # fine for pagination UI. This caps the count subquery for big result sets.
    COUNT_CEILING = 5000
    count_subq = db_query.order_by(None).limit(COUNT_CEILING + 1).subquery()
    total = db.query(func.count()).select_from(count_subq).scalar() or 0
    count_is_capped = total > COUNT_CEILING

    # Order over a BOUNDED candidate set rather than the whole match set.
    #
    # Sorting all matches let the planner walk an ordering index and filter each
    # row against the text/facet predicate. With a selective predicate that means
    # reading a large fraction of a 3.8M-row index to fill a single page: a
    # "dramione" search spent 2.0s discarding ~25k rows per worker from
    # ix_stories_kudos_desc before it found 20 matches.
    #
    # Materialising up to COUNT_CEILING matches first forces the cheap bitmap scan
    # over the GIN indexes, and sorting at most 5000 rows is trivial. It is also
    # consistent with what we already promise: the count is only exact up to that
    # same ceiling, so below it the ordering is exact and above it we order within
    # the first 5000 matches — which is what "5000+" already implies.
    candidates = db_query.order_by(None).limit(COUNT_CEILING).subquery()
    S = aliased(Story, candidates)
    ordered = db.query(S)

    sort_expr = _sort_expr(S, sort)
    if sort_expr is not None:
        ordered = ordered.order_by(sort_expr)
    elif q:
        # "Relevance" now means actual text relevance. It previously meant kudos,
        # which is meaningless on this data: 99.99% of indexed stories have kudos
        # 0, because the bulk metadata dumps carry no engagement counts at all.
        ordered = ordered.order_by(
            func.ts_rank(_story_tsv(S), func.websearch_to_tsquery(_REGCONFIG, q)).desc(),
            S.word_count.desc().nullslast(),
        )
    else:
        # Nothing to rank against. word_count is the only signal populated for
        # essentially every row (99.9%), so it beats an all-ties kudos sort.
        ordered = ordered.order_by(S.word_count.desc().nullslast())

    offset  = (page - 1) * per_page
    indexed = ordered.offset(offset).limit(per_page).all()
    indexed_cards = [_to_card(s) for s in indexed]

    # ── Live AO3 fetch ───────────────────────────────────────────────────────
    # This used to run inline and block the response. AO3's /works/search is a
    # full-text search over millions of works and takes 18-21s for a single page,
    # so three pages meant a ~30s search. It also never actually worked (see
    # live_fetch/ao3_live.py), which is the only reason nobody noticed the cost.
    #
    # Now it runs AFTER the response is sent: the reader gets indexed results in
    # milliseconds, and the fresh works land in the index for the next search.
    # For freshness on demand there is the explicit "Refresh from AO3" button,
    # which hits /api/library/refresh-ao3 and shows a spinner while it waits.
    live_cards: list[StoryCard] = []
    if live and background_tasks is not None and _should_fetch_live(sort, page, q):
        background_tasks.add_task(
            _fetch_and_persist_live,
            {
                "q": q, "fandoms": fandoms, "relationships": relationships,
                "characters": characters, "tags": tags, "ratings": ratings,
                "status": status, "word_count_min": word_count_min,
                "word_count_max": word_count_max, "sort": sort,
                "explicit": explicit,
            },
            "ao3" in active_sites,
        )

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
    fandom and a minimum word count (so you don't get art/drabbles). Biased toward
    stories with real metadata (non-zero word count) so results are readable.

    This runs on the landing page, so it has to be cheap. A plain
    `ORDER BY random() LIMIT 3` makes Postgres assign a random value to every
    candidate row and sort them all — a full sequential scan of ~1.7M rows for
    three results (~3s measured).

    Instead we sample a small slice of the table's pages with TABLESAMPLE SYSTEM and
    pick randomly within that. Percentages escalate because a selective fandom
    filter may match nothing in a small sample, and the last pass is the original
    full scan so a rare fandom still returns results rather than an empty page.
    """
    min_w = min_words or 1000
    fandom_pat = f"%{fandom.strip()}%" if fandom else None

    where = ["word_count > :min_w"]
    params: dict = {"min_w": min_w, "count": count}
    if fandom_pat:
        where.append("fic_arr(fandoms) ILIKE :fandom_pat")
        params["fandom_pat"] = fandom_pat
    where_sql = " AND ".join(where)

    # Sample IDs only, then load through the ORM so serialisation stays in _to_card
    # rather than being duplicated for raw rows. Both queries are trivially cheap.
    for pct in (0.3, 3.0):
        ids = list(db.execute(
            sql_text(
                f"SELECT id FROM stories TABLESAMPLE SYSTEM ({pct}) "
                f"WHERE {where_sql} ORDER BY random() LIMIT :count"
            ),
            params,
        ).scalars())
        if len(ids) >= count:
            rows = db.query(Story).filter(Story.id.in_(ids)).all()
            return [_to_card(s) for s in rows]

    # Fallback: whole-table scan. Only reached for filters too rare to show up in a
    # 3% page sample, where correctness matters more than the latency.
    q = db.query(Story).filter(Story.word_count > min_w)
    if fandom_pat:
        q = q.filter(_arr_text(Story.fandoms).ilike(fandom_pat))
    return [_to_card(s) for s in q.order_by(func.random()).limit(count).all()]


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
        cross_post_urls=s.cross_post_urls or [],
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
