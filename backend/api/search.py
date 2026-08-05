"""Search API — unified search across all indexed sites with hybrid live fetch"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, aliased
import os
from sqlalchemy import and_, or_, func, literal_column, cast, case, Text, text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from typing import Optional, List
from pydantic import BaseModel

from db.session import get_db
from models.story import Story, SiteEnum, RatingEnum, StatusEnum
from models.user import User, ROLE_ADMIN
from api.auth import get_current_user
from query_parser import parse_query, parsed_to_search_params
import re
from character_aliases import character_variants, relationship_variants
from language_aliases import language_variants
from provenance import content_tags, source_labels

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


# AO3 canonical fandom tags are "Work - Author"; FF.net and the older dumps use
# the bare work name. So the same fandom is split across several values:
#
#   Harry Potter                    686,558
#   Harry Potter - J. K. Rowling    381,225
#   Harry Potter - Fandom             6,884
#   Harry Potter - Rowling            5,700
#   Harry Potter - J.K. Rowling       1,395
#
# Filtering the short form already catches the rest by substring, but picking the
# AO3 canonical form — which is what autocomplete offers — excluded every story
# tagged only "Harry Potter". Matching on the part before " - " reunites them.
#
# It deliberately does NOT collapse different works: "Harry Potter and the Cursed
# Child - Thorne & Rowling" reduces to "Harry Potter and the Cursed Child", which
# is a separate work and stays separate.
_FANDOM_AUTHOR_SUFFIX = re.compile(r"\s+-\s+.+$")


def fandom_base(value: str) -> str:
    """The work name from a fandom tag, dropping any ' - Author' suffix."""
    base = _FANDOM_AUTHOR_SUFFIX.sub("", (value or "").strip())
    return base or (value or "").strip()


# ── Facet term resolution ───────────────────────────────────────────────────
#
# Facet filters were substring matches: fic_arr(fandoms) ILIKE '%Harry Potter%'.
# That needs the trigram index, and trigram is the expensive half of every
# filtered search — measured on this index, two trigram predicates cost 3,682ms
# where the equivalent array-containment lookups cost 516ms, a 7x difference,
# using GIN indexes that already exist on the arrays themselves.
#
# GitLab hit the same wall and wrote it up (gitlab-org/gitlab-ce#42442): multi
# term search over "giant trigram indexes" was their bottleneck too, and their
# measured alternative indexes were 8-10x smaller.
#
# Substring matching was there for a real reason, though: a reader typing
# "Harry Potter" expects AO3's canonical "Harry Potter - J. K. Rowling" as well.
# So rather than dropping the semantics, resolve the term against the facets
# table — which IS the site's vocabulary, already trigram-indexed, and small —
# and then match the arrays exactly against what comes back.
#
# Capped, because freeform tags explode: "Fluff" appears as a substring of
# 10,550 distinct tags ("Fluffy", "Angst with a Fluffy Ending"), and an overlap
# against 10,550 keys is slower than the trigram scan it replaced. The cap is
# ordered by work count, so it keeps the variants that actually carry works: for
# "Harry Potter" the top five cover 1,083,431 of 1,209,251 matching rows.
#
# Falls back to trigram whenever the vocabulary has nothing, so a term the
# facets table has never seen behaves exactly as before.
_FACET_KIND = {"fandoms": "fandom", "tags": "tag",
               "characters": "character", "relationships": "relationship"}

# The cap has to differ by kind, because the two behave nothing alike.
#
# FANDOMS are a bounded, curated vocabulary and readers expect umbrella
# behaviour — searching "Batman" should return the comics, the Nolan films and
# DCU, which is exactly what AO3's "All Media Types" parent tags provide and
# what r/FanFiction defends fiercely ("I don't want to have to sort through
# every single comics series, TV show, and movie"). Measured coverage of the
# works behind each term:
#
#            cap 8    cap 25   cap 60
#   Star Wars 80.9%    94.4%    98.2%   (480 variants)
#   Batman    88.3%    98.3%    99.6%   (184 variants)
#
# At 8 a Star Wars search silently missed one work in five. 60 also turned out
# to be FASTER — 1,676ms against 2,400ms — because a larger set gives the
# planner a truer picture of selectivity than a handful of keys does.
#
# FREEFORM TAGS are unbounded and must stay small: "Fluff" is a substring of
# 10,550 distinct tags ("Fluffy", "Angst with a Fluffy Ending"), and an overlap
# against that many keys is slower than the trigram scan it replaced. A reader
# choosing "Fluff" also means the Fluff tag, not everything containing the word.
FACET_VARIANT_CAP = {
    "fandom": int(os.getenv("FACET_VARIANT_CAP_FANDOM", "60")),
    "relationship": int(os.getenv("FACET_VARIANT_CAP_SHIP", "25")),
    "character": int(os.getenv("FACET_VARIANT_CAP_CHAR", "25")),
    "tag": int(os.getenv("FACET_VARIANT_CAP_TAG", "8")),
}

_VARIANT_SQL = sql_text("""
    SELECT value FROM facets
    WHERE kind = :kind AND value ILIKE :pat
    ORDER BY count DESC LIMIT :cap
""")


def _facet_variants(db, col_name: str, term: str) -> list[str]:
    """Vocabulary entries a user's term should match, most-used first."""
    kind = _FACET_KIND.get(col_name)
    if not kind:
        return []
    try:
        cap = FACET_VARIANT_CAP.get(kind, 8)
        rows = db.execute(_VARIANT_SQL, {"kind": kind, "pat": f"%{term}%",
                                         "cap": cap}).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []       # vocabulary unavailable -> caller falls back to trigram


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
    # Set only in an admin's results: the public filter removes delisted rows
    # entirely, so a card carrying this is one an operator is reviewing.
    delisted: bool = False
    # Which section of a multi-part archive this came from — FictionAlley's
    # Schnoogle / The Dark Arts / Astronomy Tower / Riddikulus / essays.
    archive_section: Optional[str] = None
    cross_post_urls: List[str] = []  # same work on other sites (deduped result)
    sources: List[str] = []          # which import(s) this row came from

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
    if spec[0] == "updated_at":
        # Sorting by updated_at alone is meaningless here: it is NULL for 99.83%
        # of rows, so "recently updated" was really "the 0.17% we have a date for,
        # then everything else in arbitrary order".
        col = func.coalesce(entity.updated_at, entity.published_at)
    else:
        col = getattr(entity, spec[0])
    return col.desc().nullslast() if spec[1] else col.asc().nullslast()


# ── Live fetch trigger logic ──────────────────────────────────────────────────

# Live-fetch throttle.
#
# Every text search schedules a 3-page AO3 fetch, and AO3's search takes 18-21s
# per page. Without a guard, reloading a page or retyping a query would fire that
# again and again for the same search, and a handful of tabs could have a dozen
# fetches running at once — hammering AO3 for results we just indexed.
#
# So: the same query is only re-fetched once per window, and only a few fetches
# run concurrently. In-memory is right for this app's single-process scale.
_LIVE_REFETCH_WINDOW = 6 * 3600     # seconds before the same query is worth redoing
_LIVE_MAX_CONCURRENT = 3
_live_last_run: dict[str, float] = {}
_live_in_flight: set[str] = set()


def _claim_live_fetch(params: dict) -> bool:
    """Reserve a live fetch for these params, or decline if it's redundant."""
    import time
    key = repr(sorted((k, v) for k, v in params.items() if v not in (None, "", False)))
    now = time.time()

    if key in _live_in_flight:
        return False
    if len(_live_in_flight) >= _LIVE_MAX_CONCURRENT:
        return False
    if now - _live_last_run.get(key, 0.0) < _LIVE_REFETCH_WINDOW:
        return False

    # Bound the memory: drop entries that are past the window anyway.
    if len(_live_last_run) > 2000:
        for k, t in list(_live_last_run.items()):
            if now - t > _LIVE_REFETCH_WINDOW:
                _live_last_run.pop(k, None)

    _live_last_run[key] = now
    _live_in_flight.add(key)
    return True


def _release_live_fetch(params: dict) -> None:
    key = repr(sorted((k, v) for k, v in params.items() if v not in (None, "", False)))
    _live_in_flight.discard(key)


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
    finally:
        _release_live_fetch(live_params)


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
    # Comma-separated archive sections, e.g. "Schnoogle,The Dark Arts".
    sections:              Optional[str] = Query(None),
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
    match_mode:            str           = Query(
        "all",
        description="How multiple values within one filter combine: 'all' (a "
                    "story must have every value — crossovers) or 'any' (a story "
                    "needs just one — variant spellings of the same fandom).",
    ),
    author:                Optional[str] = Query(
        None,
        description="Exact author match (case-insensitive). Unlike free text, this "
                    "returns only that author's works — across every archive.",
    ),
    dlp_min_rating:        Optional[float] = Query(
        None, ge=0, le=5,
        description="Minimum DarkLordPotter community star rating. DLP's list is "
                    "already curated, so this separates the best of it from the "
                    "merely-included.",
    ),
    search_within:         Optional[str] = Query(None),
    sort:                  str           = Query("relevance"),
    page:                  int           = Query(1, ge=1),
    per_page:              int           = Query(20, ge=1, le=100),
    live:                  bool          = Query(True, description="Enable hybrid live fetch"),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user),
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
    # Defaults to every site we index, not just the two big ones. The old
    # default predated FicAlley being a first-class site and silently dropped
    # it: `?sections=Schnoogle` with no `sites` returned 0, because the only
    # archive that HAS sections was not among the defaults. The frontend always
    # sends an explicit list, so nothing in the UI changes.
    active_sites = ([s.strip() for s in sites.split(",")] if sites
                    else ["ao3", "ffnet", "fictionalley"])
    site_enums   = [SiteEnum(s) for s in active_sites if s in SiteEnum.__members__]

    # ── Build DB query ────────────────────────────────────────────────────────
    db_query = db.query(Story)
    filters  = []

    # Delisted works are hidden from the public, not from the operator.
    #
    # Applied at the base of the query rather than at each call site, so no route
    # — random, related-works, live top-up — can reintroduce a row an author
    # asked to have removed. Missing one would mean the listing is gone from the
    # page the author checked and present everywhere else.
    #
    # An admin still sees them, flagged, because someone has to be able to review
    # what the auto-delist did. A request can be mistaken or malicious, and
    # hiding them from the person who has to judge them would make the reversal
    # the policy promises impossible to carry out.
    is_operator = viewer is not None and viewer.at_least(ROLE_ADMIN)
    if not is_operator:
        filters.append(Story.delisted_at.is_(None))

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

    if dlp_min_rating is not None:
        # The rating lives in a `dlp_stars:4.67` tag rather than a column, so it
        # has to be pulled out and compared numerically. That is a per-row
        # subquery, which would be unusable across 19.6M rows — but the tag only
        # exists on DLP works, and requiring `dlp_library` first narrows the scan
        # to a few hundred through the GIN index before this ever runs.
        filters.append(Story.tags.op("@>")(cast(["dlp_library"], PG_ARRAY(Text))))
        filters.append(sql_text(
            "EXISTS (SELECT 1 FROM unnest(stories.tags) AS t "
            "WHERE t LIKE 'dlp_stars:%' "
            # split_part, not substring(t FROM n) — the prefix is 10 characters
            # so the number starts at 11, and an off-by-one read '.67' as 0.67,
            # which quietly matched nothing at any threshold above zero.
            "AND split_part(t, ':', 2)::float >= :dlp_min)"
        ).bindparams(dlp_min=float(dlp_min_rating)))

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

    # "all" = a story must carry every value (crossovers, tag combinations).
    # "any" = one is enough (variant spellings of the same fandom).
    combine = or_ if match_mode == "any" else and_

    def arr_inc(col, csv_val, permissive_empty=False, normalise=None):
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
        if normalise:
            vals = [normalise(v) for v in vals]
        vals = [v for v in dict.fromkeys(vals) if v]   # dedupe, keep order
        if not vals: return None
        # Resolve each term against the site's own vocabulary and match the
        # array exactly; fall back to the trigram substring when the vocabulary
        # has nothing for it. See _facet_variants for the measurements.
        def term_match(v):
            variants = _facet_variants(db, col.key, v)
            if variants:
                # .op('&&') rather than .overlap(): the columns are declared
                # with the generic ARRAY type, whose comparator has no overlap()
                # — that is a postgresql.ARRAY method. The operator works either
                # way, and the cast keeps the parameter typed as text[] so the
                # GIN index is still eligible.
                return col.op("&&")(cast(variants, PG_ARRAY(Text)))
            return _arr_text(col).ilike(f"%{v}%")

        if permissive_empty:
            empty = or_(col.is_(None), func.cardinality(col) == 0)
            return combine(*[or_(term_match(v), empty) for v in vals])
        return combine(*[term_match(v) for v in vals])

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
        return combine(*clauses)

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
    f = arr_inc(Story.fandoms, fandoms, permissive_empty=False, normalise=fandom_base)
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
        # Match every spelling of the language, not just the one typed. AO3
        # records a work's language in that language while FF.net uses the
        # English name, so an exact match found a fraction of what exists —
        # "Chinese" returned 740 works of roughly 546,000, because the rest are
        # tagged 中文-普通话 國語.
        variants = language_variants(language)
        if variants:
            lang_match = or_(*[Story.language.ilike(v) for v in variants])
        else:
            lang_match = Story.language.ilike(language)
        filters.append(_or_unknown(lang_match, Story.language.is_(None)))
    if word_count_min:
        # NULL is unknown metadata, but a literal 0-word story is art/placeholder
        # and must always be excluded by a min-words filter.
        filters.append(_or_unknown(Story.word_count >= word_count_min, Story.word_count.is_(None)))
    if word_count_max:
        filters.append(_or_unknown(Story.word_count <= word_count_max, Story.word_count.is_(None)))
    # Date filters compare against updated_at OR, where we never captured one,
    # published_at.
    #
    # updated_at is set for 0.17% of the index (33,169 of 19.7M): the bulk dumps
    # record when a work was published but not when it was last touched. Filtering
    # "updated in the past year" strictly against that column returned almost
    # nothing — a Harry Potter / complete / >100k search went from 2,915 results to
    # 3. published_at covers 46%, so the coalesce makes the filter ~275x more
    # useful, and for a completed work the publication date is a fair proxy for
    # its last activity anyway.
    _last_activity = func.coalesce(Story.updated_at, Story.published_at)
    if updated_after:
        filters.append(_or_unknown(_last_activity >= updated_after, _last_activity.is_(None)))
    if updated_before:
        filters.append(_or_unknown(_last_activity <= updated_before, _last_activity.is_(None)))
    if published_after:
        filters.append(_or_unknown(Story.published_at >= published_after, Story.published_at.is_(None)))

    if filters:
        db_query = db_query.filter(and_(*filters))

    # Counting all matching rows on a multi-million table is the slowest part of
    # search. We only need an exact count up to a ceiling; beyond that "5000+" is
    # fine for pagination UI. This caps the count subquery for big result sets.
    # Archive sections. FictionAlley was five archives behind one banner and
    # readers navigated by them — "a Schnoogle fic" meant novel-length, "Dark
    # Arts" meant horror — so this restores a distinction the original import
    # dropped. Exact match against the stored label, since these come from a
    # closed vocabulary rather than free text.
    if sections:
        wanted = [v.strip() for v in sections.split(",") if v.strip()]
        if wanted:
            db_query = db_query.filter(Story.archive_section.in_(wanted))

    COUNT_CEILING = 5000

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
    # ONE pass, not two.
    #
    # The count and the page used to run the same expensive predicate twice —
    # a LIMIT 5001 subquery to count, then an all-but-identical LIMIT 5000
    # subquery to sort and paginate. On a filtered query that is the whole cost
    # of the search paid over again for a number.
    #
    # A window function gives the count over the same bounded candidate set the
    # page is drawn from, so the predicate runs once. The ceiling is +1 so
    # "5000+" can still be distinguished from exactly 5000.
    candidates = db_query.order_by(None).limit(COUNT_CEILING + 1).subquery()
    S = aliased(Story, candidates)
    total_over = func.count().over().label("total_matches")
    ordered = db.query(S, total_over)

    sort_expr = _sort_expr(S, sort)
    if sort_expr is not None:
        ordered = ordered.order_by(sort_expr)
    elif q:
        # "Relevance" means text relevance — it used to mean kudos, which is
        # meaningless here because 99.99% of indexed rows have kudos 0.
        #
        # But ts_rank alone got title searches badly wrong. The tsvector is one
        # undifferentiated document — fic_doc() concatenates title, summary,
        # author and every facet array — so a word in the title counts for
        # exactly as much as the same word buried in a tag list. Searching
        # "all the young dudes" put "Symphony in My Soul All" first and did not
        # return the work of that name anywhere on the first page.
        #
        # Postgres can weight fields with setweight(), but that means rebuilding
        # the tsvector definition and reindexing 19.7M rows. These expressions
        # cost nothing instead: ordering runs over the materialised candidate set
        # of at most 5,001 rows, so a per-row lower()/similarity() is trivial and
        # needs no index at all.
        #
        # Order of the tiers is the order a person means them:
        #   1  the title IS what you typed
        #   2  the title STARTS with what you typed  (…: Bootleg Tapes)
        #   3  the title contains it
        #   4  how close the whole title is, by trigram
        # then engagement, then the old text rank as the final tiebreak. Kudos
        # only ever separates rows that are already equal on title, so it cannot
        # drag a popular unrelated work above an exact match.
        q_norm = q.strip().lower()
        title_l = func.lower(S.title)
        title_rank = case(
            (title_l == q_norm, 0),
            (title_l.like(q_norm + "%"), 1),
            (title_l.like("%" + q_norm + "%"), 2),
            else_=3,
        )
        ordered = ordered.order_by(
            title_rank.asc(),
            func.similarity(func.coalesce(S.title, ""), q_norm).desc(),
            S.kudos.desc().nullslast(),
            func.ts_rank(_story_tsv(S), func.websearch_to_tsquery(_REGCONFIG, q)).desc(),
            S.word_count.desc().nullslast(),
        )
    else:
        # Nothing to rank against. word_count is the only signal populated for
        # essentially every row (99.9%), so it beats an all-ties kudos sort.
        ordered = ordered.order_by(S.word_count.desc().nullslast())

    offset  = (page - 1) * per_page
    rows    = ordered.offset(offset).limit(per_page).all()
    # count(*) OVER () repeats the same total on every row; it is only absent
    # when the page is empty, which also means there was nothing to count.
    if rows:
        total = int(rows[0][1])
    elif page > 1:
        # An empty page past the first tells us nothing about the total — the
        # window count only rides along on rows that came back. Paging beyond
        # the end would otherwise report "0 results" for a search that has
        # thousands, so fall back to counting for this uncommon case.
        total = db.query(func.count()).select_from(candidates).scalar() or 0
    else:
        total = 0
    count_is_capped = total > COUNT_CEILING
    if count_is_capped:
        total = COUNT_CEILING
    indexed = [r[0] for r in rows]
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
    # Scheduled on the event loop rather than via FastAPI's BackgroundTasks: the
    # injected `background_tasks` arrived as None here, so the task was never
    # queued and nothing was ever indexed from a live search. run_in_background
    # keeps a strong reference, so the task cannot be garbage collected mid-run.
    live_cards: list[StoryCard] = []
    if live and _should_fetch_live(sort, page, q) and "ao3" in active_sites:
        params = {
            "q": q, "fandoms": fandoms, "relationships": relationships,
            "characters": characters, "tags": tags, "ratings": ratings,
            "status": status, "word_count_min": word_count_min,
            "word_count_max": word_count_max, "sort": sort,
            "explicit": explicit,
        }
        if _claim_live_fetch(params):
            from live_fetch.jobs import run_in_background
            run_in_background(lambda: _fetch_and_persist_live(params, True))

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

    where = ["word_count > :min_w", "delisted_at IS NULL"]
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
    q = db.query(Story).filter(Story.word_count > min_w,
                               Story.delisted_at.is_(None))
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
        characters=s.characters or [],
        # Content tags only. Provenance ('ffnet_dump') is which import a row
        # came from, not what it's about, and showed up as a tag chip on 61%
        # of stories that had no real tags at all.
        tags=content_tags(s.tags), sources=source_labels(s.tags),
        warnings=s.warnings or [], categories=s.categories or [],
        genres=s.genres or [],
        published_at=s.published_at.isoformat() if s.published_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
        is_live=False,
        is_hosted=bool(s.is_hosted),
        # Only ever non-null in an admin's results — the public filter above
        # removes these rows entirely, so a card carrying this flag is one an
        # operator is looking at in order to decide whether to reverse it.
        delisted=s.delisted_at is not None,
        archive_section=s.archive_section,
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
        characters=d.get("characters", []),
        tags=content_tags(d.get("tags")), sources=source_labels(d.get("tags")),
        warnings=d.get("warnings", []), categories=d.get("categories", []),
        genres=d.get("genres", []),
        published_at=d.get("published_at"), updated_at=d.get("updated_at"),
        is_live=d.get("is_live", True),
    )
