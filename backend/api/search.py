"""Search API — unified search across all indexed sites with hybrid live fetch"""
import hashlib
import logging
from functools import lru_cache
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session, aliased
import os
from sqlalchemy import and_, or_, func, literal_column, cast, case, Text, text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from typing import Dict, Optional, List
from pydantic import BaseModel

from db.session import get_db
from models.story import Story, SiteEnum, RatingEnum, StatusEnum
from models.user import User, ROLE_ADMIN
from api.auth import get_current_user
from search_cache import CACHE, cache_key, shared_get, shared_put

# Shorter than the 60s session default — see the note in search() on why a long
# timeout is what turns a slow search into a failed one.
SEARCH_TIMEOUT_MS = int(os.getenv("SEARCH_STATEMENT_TIMEOUT_MS", "20000"))

# How many matching rows a search materialises before ranking and counting.
# See the note at its use in search() — this is the dominant term in how much
# disk a search touches.
SEARCH_COUNT_CEILING = int(os.getenv("SEARCH_COUNT_CEILING", "5000"))

# A lower ceiling, used ONLY by the ordered-browse fast path and only for its
# COUNT. Counting is where the time goes once the rows are an index walk, and the
# cost is wildly non-linear because the scan has to read far enough to find that
# many matching rows. Measured on `fandoms=Harry Potter`, same query, id-only
# subquery, this box:
#
#     LIMIT 5001   17,977 ms
#     LIMIT 2000    1,711 ms
#     LIMIT 1000      946 ms
#     LIMIT  500      271 ms
#
# 2,000 is the knee. It is deliberately NOT the global ceiling: SEARCH_COUNT_
# CEILING also sizes the candidate set a TEXT search ranks over, and shrinking
# that would quietly make relevance worse to make a browse faster. A browse has
# no ranking to damage — it is already in index order — so the only thing this
# costs is that a filtered browse says "2,000+" where it used to say "5,000+",
# and both mean "more than you will page through".
SEARCH_BROWSE_COUNT_CEILING = int(os.getenv("SEARCH_BROWSE_COUNT_CEILING", "2000"))

# How long a filtered browse's total is reused for. Longer than the response
# cache's 120s because the number is capped and therefore coarse — "2,000+" does
# not become wrong in ten minutes — and because it is the expensive half of the
# request. See the count cache in search() for the measurements.
COUNT_CACHE_TTL = int(os.getenv("SEARCH_COUNT_CACHE_TTL", "900"))

# Cap on the "N explicit works are hidden" count. Far lower than the search
# ceiling because the number is a hint, not a result set: "999+" and "4,300" tell
# a reader the same thing, and the count only runs on searches that returned
# nothing, where the filters are by definition unselective enough to have matched
# zero rows and a large scan is the very thing to avoid.
HIDDEN_EXPLICIT_CEILING = int(os.getenv("HIDDEN_EXPLICIT_CEILING", "999"))

# A title ending on one of these was cut off, not written that way.
#
# The English half is the set ao3_title_repair.py already trusts to identify a
# truncated dump title ("no real title ends there"), widened to the rest of the
# closed-class words that behave the same way. The Iberian and Slavic articles
# are here because the dump truncated non-English titles too — "Leyendo Harry
# Potter la" and "Lembre-se de nos como" are the same defect, and an
# English-only rule leaves those on the page.
#
# Only closed-class words, never content words: "Sobrevivientes Tercera" is
# also truncated and is NOT matched here, because a rule that catches it would
# catch real titles as well. Under-matching is the right direction — the cost
# is a broken title still showing, where over-matching hides a real work.
_BROKEN_TITLE_TAIL = (
    r" (and|of|the|with|or|to|in|for|from|by|at|on|a|an|but|as|into|about"
    r"|la|el|los|las|un|una|de|del|y|e|o|que|con|por|para|como|na|nos|dos|das"
    r"|no|i|w|z|na)$"
)

# Matches the in-process TTL, so the two layers cannot disagree about how stale
# a result may be.
SEARCH_CACHE_SECONDS = int(os.getenv("SEARCH_CACHE_SECONDS", "120"))
from query_parser import parse_query, parsed_to_search_params
import re
from character_aliases import character_variants, relationship_variants
from language_aliases import language_variants
from provenance import content_tags, source_labels
import author_permission

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

# Expand a term to every spelling of it that the index actually contains.
#
# This was `value ILIKE :pat`, which folds case and not punctuation — so
# searching Hurt/Comfort never matched the 33 works tagged hurt-comfort,
# hurt comfort, hurt & comfort or hurt|comfort, and "Fluff" missed "fluff!",
# "#fluff" and "F.L.U.F.F.". The normalised key (see init_db.py) catches those:
# same letters and digits, any punctuation.
#
# The ILIKE arm is kept alongside it, because the two find different things.
# Normalised equality finds other SPELLINGS of the same tag; ILIKE finds tags
# that CONTAIN the term, which is what makes searching "Fluff" also reach
# "Fluff and Angst". Dropping either one loses results.
_VARIANT_SQL = sql_text("""
    SELECT value FROM facets
    WHERE kind = :kind
      AND (value ILIKE :pat
           OR norm = regexp_replace(lower(:term), '[^a-z0-9]+', '', 'g'))
    ORDER BY count DESC LIMIT :cap
""")


@lru_cache(maxsize=4096)
def _facet_weight_cached(term: str) -> int:
    return -1        # replaced at runtime; see _query_is_category


def _query_is_category(db, term: str) -> int:
    """How many works carry this exact phrase as a fandom, ship, character or tag.

    The discriminator between "find me the work called X" and "find me works
    ABOUT X", which need opposite rankings and are indistinguishable from the
    text alone:

        living with danger   no facet at all      -> a title
        all the young dudes  tag, 13 works        -> a title
        dramione             ship, 134 works      -> a category
        harry potter         fandom, 686k works   -> a category

    Getting this wrong is visible in both directions. Treating "harry potter" as
    a title returned works literally named that with no readers, above every
    real Harry Potter story. Treating "living with danger" as a category buried
    the one work anybody means under everything sharing a word with it.

    A heuristic, and it will be wrong for a work deliberately titled after its
    own ship. It is wrong less often than having no signal at all, and the
    ordering degrades rather than breaks — a category query still ranks title
    matches highly, it just stops ranking them above everything else.
    """
    try:
        return int(db.execute(sql_text(
            "SELECT COALESCE(max(count), 0) FROM facets WHERE lower(value) = :v"
        ), {"v": term}).scalar() or 0)
    except Exception:
        return 0


_SHIP_SPLIT = re.compile(r"\s*/\s*")


def _both_orders(term: str) -> list[str]:
    """A pairing written both ways round, or just the term if it isn't one.

    The vocabulary lookup below is a substring match, so it only finds the order
    the reader happened to type — and the archives are not consistent about it.
    Two halves only: reversing a three-way pairing has six orderings and no
    single obvious one, and those tags are rare enough not to be worth it.
    """
    parts = [p for p in _SHIP_SPLIT.split(term.strip()) if p]
    if len(parts) != 2:
        return [term]
    return [term, f"{parts[1]}/{parts[0]}"]


def _facet_variants(db, col_name: str, term: str) -> list[str]:
    """Vocabulary entries a user's term should match, most-used first."""
    kind = _FACET_KIND.get(col_name)
    if not kind:
        return []
    try:
        cap = FACET_VARIANT_CAP.get(kind, 8)
        rows = db.execute(_VARIANT_SQL, {"kind": kind, "pat": f"%{term}%",
                                         "term": term, "cap": cap}).fetchall()
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
    # The author proved control of their archive account and asked us to host
    # their work. Shown as a mark on the card — see api/permissions.py.
    author_verified: bool = False
    is_hosted: bool = False        # true = full text stored locally, one-click reader
    # Series membership, attached after the page is selected — see _attach_series.
    # Not part of the main query: most works are in none, so joining for every
    # row would be cost paid overwhelmingly for nulls.
    series_name: Optional[str] = None
    series_id: Optional[str] = None
    series_position: Optional[int] = None
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
    # How the matches break down by archive, e.g. {"ao3": 890, "ffnet": 340}.
    #
    # This is the one thing FicAtlas can show that no single archive can: that a
    # search found work in three places at once. The landing page has always
    # asserted it ("one search across all three, instead of three searches that
    # each miss two"); until now nothing demonstrated it at the point where the
    # reader could actually check.
    #
    # Counted over the same bounded candidate set as `total`, so when
    # `count_is_capped` is true this is the split of the capped sample rather
    # than of every match — the UI says so rather than implying precision.
    site_counts: Dict[str, int] = {}
    live_count: int = 0            # how many results came from live fetch
    # Matches the explicit filter is hiding, counted only when the page came back
    # empty — see the note where it is computed. Capped, so the UI says "999+"
    # rather than quoting a number this never measured.
    hidden_explicit: int = 0
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
    # The only sort that can be asked of the whole index rather than of one
    # archive at a time. Written offline by popularity_rank.py — a percentile
    # within each site, so an AO3 kudos and an FF.net favourite stop being
    # compared as though they were the same unit. NULL for a work with no
    # recorded engagement, which nullslast() puts after everything scored rather
    # than ranking it as unpopular.
    "popularity_desc": ("popularity", True),
}


# The predicate that matches each partial sort index, so an unfiltered browse can
# use it as a top-N walk instead of sorting 20M rows. Keyed by sort, applied ONLY
# when the search is not narrowed — see the long note at the call site. A sort
# with no entry here is backed by a full index (kudos, word_count) or by the
# coalesce expression index (the date sorts), and needs no predicate.
# How to order an UNFILTERED browse so the ordering matches an actual index.
#
# (predicate, order-expression). The predicate removes the NULLs; the expression
# is then a plain .desc(), which is what the index stores.
#
# Both halves are load-bearing, and the second one is the subtle one: a DESC
# btree is NULLS FIRST by default, so ordering by `col DESC NULLS LAST` — which
# is what _sort_expr produces, correctly, for the general case — cannot use it
# to order at all and Postgres sorts the whole table instead. That is not a
# small difference at 20M rows: "Most hits" measured 32.7s that way against
# 0.06s once the ordering matched, and "Most kudos" and "Longest" hit the 60s
# statement timeout outright and returned 503.
#
# A sort that is NOT in this map keeps the old cap-then-order behaviour. That is
# deliberate: pre-ordering without an index that supports it is how those 503s
# happened, so the rule is "only where it is known to be an index walk", never
# "by default and hope".
_BROWSE_ORDER = {
    "published_desc":  (lambda e: e.published_at.isnot(None), lambda e: e.published_at.desc()),
    "kudos_desc":      (lambda e: e.kudos.isnot(None),        lambda e: e.kudos.desc()),
    "hits_desc":       (lambda e: e.hits > 0,                 lambda e: e.hits.desc()),
    "comments_desc":   (lambda e: e.comments > 0,             lambda e: e.comments.desc()),
    "bookmarks_desc":  (lambda e: e.bookmarks > 0,            lambda e: e.bookmarks.desc()),
    "word_count_desc": (lambda e: e.word_count.isnot(None),   lambda e: e.word_count.desc()),
    # updated_desc is absent on purpose: ix_stories_last_activity is itself
    # `COALESCE(updated_at, published_at) DESC NULLS LAST`, so _sort_expr already
    # matches it exactly and needs no predicate. Handled below.
}

# The lowest value a timestamptz can hold. Used as a range bound that selects
# exactly what `IS NOT NULL` selects, while giving the planner a start position
# in the btree — see the fast path in search() for the 6,649ms -> 0.17ms this is
# worth. `-infinity` is a real timestamptz value, so this is not a sentinel date
# that could one day collide with real data.
_TS_MIN = "-infinity"

# Sorts the fast path can serve, as (range-bound predicate, ORDER BY expression).
#
# Both entries are index-shaped and neither is interchangeable with _sort_expr:
#
#   * the BOUND must select exactly the non-NULL rows and nothing else, or the
#     fast path would silently drop matches. Every bound here is equivalent to
#     `IS NOT NULL` for its column — kudos and word_count are never negative
#     (checked, 0 rows each), and no timestamp is below -infinity.
#   * the ORDER BY must match the index's own ordering EXACTLY, including where
#     it puts NULLs, or Postgres cannot walk it and sorts instead:
#       ix_stories_last_activity   COALESCE(...) DESC NULLS LAST  -> .nullslast()
#       ix_stories_published_desc  published_at DESC (partial)    -> plain .desc()
#       ix_stories_kudos_desc      kudos DESC                     -> plain .desc()
#       ix_stories_word_count      word_count (ASC, walked back)  -> plain .desc()
#     Getting this wrong is not subtle in cost: published_desc ordered with
#     NULLS LAST against its plain-DESC partial index measured 21.2s, and 1.6s
#     with the ordering matched.
#
# hits/comments/bookmarks are deliberately absent. Their indexes are partial on
# `col > 0`, so the bound that would make them usable is `> 0` — and that DOES
# drop matches: a filtered search for "most hits" must still list works with no
# recorded hits, after the ones that have them, rather than pretending they do
# not exist. See the popularity_desc note above for the same trap.
_FAST_SORTS = {
    "updated_desc":   (lambda e: func.coalesce(e.updated_at, e.published_at) >= _TS_MIN,
                       lambda e: func.coalesce(e.updated_at, e.published_at).desc().nullslast()),
    "published_desc": (lambda e: e.published_at >= _TS_MIN,
                       lambda e: e.published_at.desc()),
    # kudos_desc and word_count_desc were removed from this map once, on the
    # strength of `tags=Fluff&sort=kudos_desc` measuring 47.3s. That measurement
    # was taken while the planner believed `stories` held 84,799 rows instead of
    # 20.1M — ANALYZE had never run — so it was not measuring these sorts at all,
    # it was measuring a planner with no statistics. Re-measured afterwards, with
    # the same queries: word_count 10.2ms (Harry Potter) and 10.1ms (Fluff),
    # kudos 4.9ms and 19.0ms. They belong here.
    #
    # Both are lossless: the bound is `>= 0` on columns that are never negative
    # (checked, 0 rows each), so it selects exactly what IS NOT NULL selects, and
    # their indexes are FULL rather than partial — nothing is excluded from the
    # ordering the way a `> 0` bound against a partial index would exclude it.
    #
    # word_count's index is ASC and is walked backwards for DESC; kudos's is
    # already DESC. Both therefore want a plain .desc() with no nullslast, which
    # is what matches. hits/comments/bookmarks are still absent, and for the
    # different reason spelled out above.
    "kudos_desc":      (lambda e: e.kudos >= 0,      lambda e: e.kudos.desc()),
    "word_count_desc": (lambda e: e.word_count >= 0, lambda e: e.word_count.desc()),
}

# Sorts whose own index already matches _sort_expr, so they pre-order with no
# predicate and no special casing.
_BROWSE_ORDER_DIRECT = {"updated_desc"}


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

def _apply_cache_headers(response, viewer) -> None:
    """Tell a shared cache in front of us what it may do with this response.

    Every exit from search() must call this, including the cache hits. It used to
    be set once at the bottom, which meant the two early returns for an L1 or
    shared-tier hit answered with no Cache-Control at all — and those are exactly
    the popular, repeated queries most worth caching at the edge. Cloudflare saw
    the first (slow, uncached) answer as cacheable and every subsequent one as
    DYNAMIC, so the queries that would have benefited most were the only ones the
    edge refused to keep. The design defeated itself precisely where it should
    have paid off.

    Only for anonymous readers. A signed-in viewer may be an operator, whose
    results include delisted works, and even an ordinary account is a viewer
    whose response must never be handed to somebody else by a shared proxy.
    `private` on that branch says exactly that: the browser may keep it, no
    shared cache may.
    """
    if response is None:
        return
    if viewer is None:
        response.headers["Cache-Control"] = (
            f"public, max-age={SEARCH_CACHE_SECONDS}, "
            f"stale-while-revalidate={SEARCH_CACHE_SECONDS * 4}")
    else:
        response.headers["Cache-Control"] = "private, no-store"


def _note_total(request, payload) -> None:
    """Hand the result count to the traffic recorder.

    The middleware in main.py sees the request and the status code but not the
    body, and "how many did this search find" is the one number that turns a
    list of popular queries into a list of gaps in the index. Left on
    request.state rather than passed anywhere: a new exit from search() that
    forgets this records the query with an unknown count, which the report shows
    as "—". Nothing breaks and nothing lies.

    Called BEFORE _apply_cache_headers at each exit, never between it and the
    return: test_search_cache_headers walks the ast and requires the cache-header
    call to be the statement immediately preceding every `return`, which is what
    stops a future exit from quietly losing it.
    """
    try:
        total = getattr(payload, "total", None)
        # `or 0` here would turn "no count available" into "found nothing", which
        # is the one confusion the NULL exists to prevent — a healthy query would
        # appear in the empty-results report and send the crawler after something
        # already indexed.
        if total is None:
            return
        request.state.search_total = int(total)
    except Exception:
        pass


@router.get("", response_model=SearchResponse)
def search(          # NOT async — see below
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
    # Authors, which the array excludes above cannot express: author is a scalar
    # column, not a tag array. Needed for a reader's persistent mute list, where
    # "never show me this writer" is one of the two things people actually want
    # (the other being ships, which exclude_relationships already covers).
    exclude_authors:       Optional[str] = Query(None),
    status:                Optional[str] = Query(None),
    # Comma-separated archive sections, e.g. "Schnoogle,The Dark Arts".
    sections:              Optional[str] = Query(None),
    in_series:             Optional[bool] = Query(
        None,
        description="true = only works that belong to a series, false = only "
                    "standalones. Omitted means no preference."),
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
    include_broken_titles: bool          = Query(
        False,
        description="Also return works whose stored title is truncated or "
                    "missing ('Riding on Brooms With', 'unknown'). Hidden by "
                    "default — see _BROKEN_TITLE_TAIL.",
    ),
    sort:                  str           = Query("relevance"),
    page:                  int           = Query(1, ge=1),
    per_page:              int           = Query(20, ge=1, le=100),
    live:                  bool          = Query(True, description="Enable hybrid live fetch"),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user),
    request: Request = None,
    response: Response = None,
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
        # `author:` from the search bar. An explicit ?author= still wins, the
        # same rule every other operator follows.
        if not author        and parsed_params.get("author"):        author        = parsed_params["author"]
        if word_count_min is None and parsed_params.get("word_count_min"): word_count_min = parsed_params["word_count_min"]
        if word_count_max is None and parsed_params.get("word_count_max"): word_count_max = parsed_params["word_count_max"]
        if not updated_after and parsed_params.get("updated_after"): updated_after = parsed_params["updated_after"]
        if not crossovers    and parsed_params.get("crossovers"):    crossovers    = parsed_params["crossovers"]
        if not sites         and parsed_params.get("sites"):         sites         = parsed_params["sites"]
        if not exclude_fandoms       and parsed_params.get("exclude_fandoms"):       exclude_fandoms       = parsed_params["exclude_fandoms"]
        if not exclude_relationships and parsed_params.get("exclude_relationships"): exclude_relationships = parsed_params["exclude_relationships"]
        if not exclude_characters    and parsed_params.get("exclude_characters"):    exclude_characters    = parsed_params["exclude_characters"]
        if not exclude_tags          and parsed_params.get("exclude_tags"):          exclude_tags          = parsed_params["exclude_tags"]
        # series:true / series:false in the search bar. Explicit ?in_series=
        # still wins, matching every other operator.
        if in_series is None and parsed_params.get("in_series") is not None:
            in_series = parsed_params["in_series"]

        # Replace q with just the clean free text
        q = pq.clean_text or None

    # ── Site list ─────────────────────────────────────────────────────────────
    # Defaults to every site we index, not just the two big ones. The old
    # default predated FictionAlley being a first-class site and silently dropped
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

    # Works whose title we hold in a visibly broken state.
    #
    # The bulk AO3 dump ships titles cut mid-phrase — "Riding on Brooms With",
    # "Leyendo Harry Potter la" — and some sources gave us no title at all
    # ("unknown"). 688,000 rows, 3.4% of the index, and they are the entries that
    # make a results list look broken: a reader cannot tell a truncated title
    # from a bad index.
    #
    # ao3_title_repair.py fixes these properly by re-fetching the work, and the
    # worker runs it — but it is one AO3 request per work against ~400k
    # candidates, so it is a slow grind rather than a backfill that finishes.
    # Hiding them is the interim answer, not the repair.
    #
    # A dangling function word cannot end a real title, which is the same
    # confidence ao3_title_repair.py relies on to identify these at all. The
    # non-English articles are here because the truncation is not English-only.
    # Reversible per request rather than a policy: `include_broken_titles=true`
    # brings them back, so nothing is unreachable — an exact-title search that
    # wants them can still ask.
    if not include_broken_titles:
        filters.append(and_(
            Story.title.isnot(None),
            func.btrim(Story.title) != "",
            ~func.lower(func.btrim(Story.title)).in_(
                ["unknown", "untitled", "no title", "n/a"]),
            ~Story.title.op("~*")(_BROKEN_TITLE_TAIL),
        ))

    # Fail fast rather than holding a connection for a minute.
    #
    # The session default is a 60s statement timeout, which is right for a bulk
    # script and wrong here: a search that is going to be slow holds one of the
    # pool's connections for that whole minute, and twelve of those wedge a
    # worker. Load testing at 8 concurrent searches returned 500 for 375 of 612
    # requests — not slow, failed — because the pool filled with queries nobody
    # was still waiting for.
    #
    # A shorter ceiling turns that into load shedding: the query is abandoned
    # while the reader is still plausibly waiting, the connection goes back, and
    # the next request gets it. SET LOCAL so it applies to this transaction only
    # and cannot leak to whatever reuses the connection.
    try:
        db.execute(text("SET LOCAL statement_timeout = :ms"),
                   {"ms": SEARCH_TIMEOUT_MS})
    except Exception:
        pass  # a missing timeout is slower, not broken

    # Search is the one endpoint bound by disk rather than CPU (see
    # search_cache.py), so a repeated query is the only cheap capacity there is.
    # Keyed on the visibility flag as well as the parameters: an operator sees
    # delisted rows and must never share an entry with the public.
    _ck = None
    if request is not None:
        _ck = cache_key(str(request.url.query), is_operator)
        _hit = CACHE.get(_ck)
        if _hit is not None:
            _note_total(request, _hit)
            _apply_cache_headers(response, viewer)
            return _hit
        # L1 missed. Before paying for the query, ask the shared tier: with four
        # workers this is usually a sibling that has already done exactly this
        # search. ~1ms to ask, ~10s to be wrong about it.
        _shared = shared_get(db, _ck)
        if _shared is not None:
            try:
                _hit = SearchResponse.model_validate_json(_shared)
                CACHE.put(_ck, _hit)   # promote, so the next one skips both hops
                _note_total(request, _hit)
                _apply_cache_headers(response, viewer)
                return _hit
            except Exception:
                # A stale entry from before a response-shape change. Fall through
                # and recompute rather than serving something malformed.
                pass

    if site_enums:
        filters.append(Story.site.in_(site_enums))

    # Kept as a named object, not just appended, so the count of what it hid can
    # be rebuilt later from the same filter list minus this one predicate. See
    # `hidden_explicit` below.
    _explicit_pred = None
    if not explicit:
        # Hide only stories KNOWN to be explicit. `rating != 'E'` is NULL (not TRUE)
        # for NULL-rating rows in SQL, which silently dropped the entire NULL-rating
        # bulk import (HF FFN dump etc.) from every default search. Permit NULL.
        _explicit_pred = or_(Story.rating != RatingEnum.explicit, Story.rating.is_(None))
        filters.append(_explicit_pred)

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
        #
        # Mild spelling tolerance lives in the title-candidate UNION below, not
        # here. An OR similarity() against the whole table forced a seq scan of
        # 19.7M titles and turned a 200ms search into a 60s timeout.
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
        
    Deliberately a plain `def`, not `async def`.

    Every query in here is blocking psycopg2 through SQLAlchemy. Declared
    `async def`, that ran on the event loop thread — so one search froze the
    entire server, including other people's searches and the health check.
    Measured before the change: a query that takes 0.34s alone took 8.3s median
    with sixteen callers, scaling almost perfectly linearly, which is what
    complete serialisation looks like.

    As a plain `def`, FastAPI runs it in a worker thread and the loop stays free.
    The right long-term answer is an async driver; this is the one-word version
    of it that does not mean rewriting every query.

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
            if not variants:
                # The alias table covers ~40 Harry Potter characters and returns
                # [] for a pairing where EITHER half is unknown — which is most
                # of the index. That dropped straight to the substring branch
                # below, and the trigram index backing it
                # (ix_stories_relationships_trgm) was deleted as unused, so the
                # "fallback" became a sequential scan of 20M rows.
                #
                # Measured on "Theodore Nott/Luna Lovegood", 7 works:
                # 83.5s at the backend and a 500 through the proxy, for a filter
                # every ship hub's "Search all" link and every ship tag emits.
                #
                # Resolving against the site's own vocabulary first is what
                # arr_inc already does for fandoms and tags, and it turns this
                # into the GIN array lookup it was always supposed to be.
                #
                # BOTH ORDERS, because the archives disagree about which half of
                # a pairing goes first and the vocabulary lookup is a substring
                # match, so it only ever finds the order that was typed.
                # "Theodore Nott/Luna Lovegood" resolved to 3 works while
                # "Luna Lovegood/Theodore Nott" — the same ship — carried 564,
                # and nothing on the page suggested the other 564 existed. This
                # is the order-independence the ship hubs already have; search
                # simply never got it.
                variants = []
                for term in _both_orders(v):
                    for x in _facet_variants(db, col.key, term):
                        if x not in variants:
                            variants.append(x)
            if variants:
                match = col.op("&&")(cast(variants, PG_ARRAY(Text)))
            else:
                # Last resort, and genuinely slow now that the trigram indexes on
                # these columns are gone. Only reached for a term the vocabulary
                # has never seen, which by definition matches nothing much.
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

    # Case-insensitive and exact: a mute list holds pen names as typed, and
    # "MsKingBean89" must not also hide "mskingbean89_archive". NULL authors are
    # kept — an unattributed row is not the muted author.
    if exclude_authors:
        muted = [a.strip().lower() for a in exclude_authors.split(",") if a.strip()]
        if muted:
            filters.append(or_(Story.author.is_(None),
                               func.lower(Story.author).notin_(muted)))

    # Scalar filters follow the same rule as the facet filters: a story only matches
    # if it actually carries the value. `include_unknown` re-admits rows where the
    # field is NULL (metadata we never captured), rather than that being the default.
    def _or_unknown(cond, *unknown_conds):
        return or_(cond, *unknown_conds) if include_unknown else cond

    # Tracked like _explicit_pred, and for the same reason — see hidden_explicit.
    #
    # The search UI sends ratings=G,T,M,NR on EVERY search with the explicit
    # toggle off, as a second expression of the same intent. That is harmless for
    # results (no row has a NULL rating, so it selects exactly the non-explicit
    # ones) and was fatal for the count: dropping only the explicit predicate
    # left `rating IN (G,T,M,NR) AND rating = E`, which is 0 by construction. So
    # the notice never appeared in the real UI, while appearing correctly for a
    # hand-made request that omitted the parameter.
    _ratings_pred = None
    _ratings_is_all_but_explicit = False
    if ratings:
        r_vals = [r.strip().upper() for r in ratings.split(",")]
        valid  = [RatingEnum(r) for r in r_vals if r in RatingEnum._value2member_map_]
        if valid:
            _ratings_pred = _or_unknown(Story.rating.in_(valid), Story.rating.is_(None))
            filters.append(_ratings_pred)
            # Only the synthetic "everything except explicit" set is treated as
            # the toggle restated. A reader who deliberately ticked Teen alone
            # has asked for Teen, and turning the explicit toggle on would not
            # reveal anything — so their filter stays, and the count is 0, which
            # is the truth.
            _ratings_is_all_but_explicit = (
                set(valid) == set(RatingEnum) - {RatingEnum.explicit})

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
    # Dates are coerced, and an unparseable one DROPS its filter rather than
    # reaching the database.
    #
    # These three arrive as raw strings and were interpolated straight into a
    # timestamptz comparison, so `?updated_after=notadate` reached Postgres as
    # `>= 'notadate'` and came back as
    #     DataError: invalid input syntax for type timestamp with time zone
    # which FastAPI turns into a 500 on a public, unauthenticated endpoint. Any
    # crawler mangling a URL, or anyone editing one by hand, could trigger it.
    #
    # Dropping the filter matches how the rest of this endpoint treats input it
    # cannot use — an unrecognised archive drops the site filter rather than
    # keeping one nothing can match — and it reuses query_parser's own date
    # rules, so `2024`, `30d` and `2024-01-31` mean the same thing typed into
    # the search bar as passed in the URL.
    from query_parser import _parse_date as _coerce_date
    updated_after   = _coerce_date(updated_after)   if updated_after   else None
    updated_before  = _coerce_date(updated_before)  if updated_before  else None
    published_after = _coerce_date(published_after) if published_after else None

    _last_activity = func.coalesce(Story.updated_at, Story.published_at)
    if updated_after:
        filters.append(_or_unknown(_last_activity >= updated_after, _last_activity.is_(None)))
    if updated_before:
        filters.append(_or_unknown(_last_activity <= updated_before, _last_activity.is_(None)))
    if published_after:
        filters.append(_or_unknown(Story.published_at >= published_after, Story.published_at.is_(None)))

    # Part of a series, or deliberately not. Two quite different searches: one
    # reader wants something to sink into for a month, another wants a thing
    # they can finish tonight without acquiring a reading list.
    #
    # Plain column, not EXISTS against series_works. The EXISTS form seq-scanned
    # series_works and nested-looped every member into stories — 15s for Harry
    # Potter + AO3/FFN + in_series, against 1.5s without it. has_series is kept
    # in sync by series_detect and ao3_series.record.
    if in_series is not None:
        filters.append(Story.has_series.is_(True) if in_series
                       else Story.has_series.is_(False))

    if filters:
        db_query = db_query.filter(and_(*filters))

    # Has the reader narrowed this search at all, as opposed to browsing the
    # whole index? Only used to decide whether "Most popular" may take its fast
    # path — see the popularity_desc branch below.
    #
    # Deliberately a list of the parameters rather than `len(filters)`: the
    # filter list always carries the delisted/restricted gates and usually the
    # rating one, so its length says nothing about whether the READER asked for
    # anything. Site selection is excluded for the same reason — the UI sends all
    # three by default, so it is not a narrowing choice.
    _narrowed = any([
        q, fandoms, characters, relationships, tags, warnings, categories,
        author, status, language, sections, dlp_min_rating,
        word_count_min, word_count_max,
        updated_after, updated_before, published_after,
        exclude_fandoms, exclude_characters, exclude_relationships,
        exclude_tags, exclude_ratings, exclude_warnings, exclude_categories,
        exclude_authors,
        in_series is not None,
        crossovers not in (None, "include"),
        # The synthetic "everything but explicit" set is the content toggle
        # restated, not a choice about ratings — see _ratings_is_all_but_explicit.
        bool(ratings) and not _ratings_is_all_but_explicit,
    ])

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

    # Env-configurable so it can be measured rather than argued about. Lowering
    # it is the one lever that attacks the actual bottleneck — search is bound by
    # heap reads, and this is exactly how many rows get read — at the cost of a
    # lower "N+" figure and a ranker that sees fewer candidates.
    COUNT_CEILING = SEARCH_COUNT_CEILING
    # Enough to hold every work sharing a title — 73 are called "All the Young
    # Dudes" — without letting a generic prefix flood the ranked set.
    TITLE_CANDIDATES = 300
    # Best-read matches for a broad query, so ranking sees them at all.
    POPULAR_CANDIDATES = 300

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
    # An exact title match must always be in the candidate set.
    #
    # The set is an ARBITRARY 5,001 rows — deliberately unordered, because
    # ordering the full match set is the expensive thing this design avoids. But
    # that means for any query matching more than 5,000 works, ranking never
    # sees most of them, and the best answer can simply be absent.
    #
    # Real example: "living with danger" matched 5,000+ and returned "Living in
    # Danger", "Dancing with Danger" and "Living For Danger" — while the work
    # actually called Living with Danger, by whydoyouneedtoknow, was nowhere. Add
    # author=whydoyouneedtoknow and the match set drops to 14, all of them get
    # ranked, and it comes first. The ranking was right the whole time; it was
    # being handed the wrong rows.
    #
    # So title matches are fetched separately and unioned in. With the
    # lower(title) index that lookup is 0.33ms, against 24s before it existed.
    if q and q.strip():
        q_norm = q.strip().lower()
        words = q_norm.split()
        title_pred = or_(
            func.lower(Story.title) == q_norm,
            func.lower(Story.title).like(q_norm + "%"),
        )
        # Near-miss titles FTS will miss (a typo). Plurals are already handled
        # by the english stemmer ("dude" matches "Dudes"); this is for
        # "arithmnacer" → "The Arithmancer".
        #
        # similarity() itself is unindexed, so the slice it runs over MUST be
        # served by ix_stories_title_lower. A LIKE 'arit%' predicate made the
        # planner seq-scan 19.7M rows (13s); a range condition
        # (lower(title) >= 'arit' AND < 'ariu') is an index scan (8ms).
        #
        # Applied on a SEPARATE query that does NOT carry the FTS predicate —
        # otherwise a typo that fails FTS can never enter via this branch.
        fuzzy_pred = None
        if (len(words) == 1 and len(q_norm) >= 6
                and q_norm.isalpha()
                and not re.search(r'[:"()\-]', q_norm)):
            # Titles often lead with an article ("The Arithmancer"). Range-scan
            # each common article+prefix so the btree still fires; without this
            # "arithmnacer" only reached works literally titled "Arithmancer".
            article_ranges = []
            for art in ("", "the ", "a ", "an "):
                p = art + q_norm[:4]
                n = p[:-1] + chr(ord(p[-1]) + 1)
                article_ranges.append(and_(
                    func.lower(Story.title) >= p,
                    func.lower(Story.title) < n,
                    func.similarity(func.lower(Story.title), art + q_norm) >= 0.5,
                ))
            fuzzy_pred = or_(*article_ranges)
        elif (3 <= len(words) <= 8 and len(q_norm) >= 10
                and not re.search(r'[:"()\-]', q_norm)):
            # Multi-word: bound by the first two words as a range prefix.
            # Skipped for very common openers ("the", "a", "all the") that
            # would still touch tens of thousands of titles.
            prefix = " ".join(words[:2])
            if prefix not in ("the", "a", "an", "all the", "to the", "in the",
                              "of the", "for the", "on the"):
                nxt = prefix[:-1] + chr(ord(prefix[-1]) + 1) if prefix else None
                if nxt:
                    fuzzy_pred = and_(
                        func.lower(Story.title) >= prefix,
                        func.lower(Story.title) < nxt,
                        func.similarity(func.lower(Story.title), q_norm) >= 0.5,
                    )
        parts = [
            db_query.order_by(None).filter(title_pred).limit(TITLE_CANDIDATES),
            db_query.order_by(None).limit(COUNT_CEILING + 1),
        ]
        if fuzzy_pred is not None:
            # Structural filters only — same site/explicit/delisted gate as the
            # main query, but no FTS, so a near-miss title is allowed in.
            fuzzy_q = db.query(Story).filter(Story.delisted_at.is_(None))
            if site_enums:
                fuzzy_q = fuzzy_q.filter(Story.site.in_(site_enums))
            if not explicit:
                fuzzy_q = fuzzy_q.filter(or_(
                    Story.rating != RatingEnum.explicit, Story.rating.is_(None)))
            parts.append(fuzzy_q.filter(fuzzy_pred).limit(50))
        # For a BROAD query the arbitrary slice is the whole problem. Searching
        # "harry potter" matches far more than the ceiling, so the 5,001 rows the
        # ranker sees are an arbitrary sample of 686,000 — and the works everyone
        # actually means are almost certainly not among them. No amount of
        # rescoring fixes being handed the wrong rows.
        #
        # So the best-read matches are fetched explicitly. This is cheap for
        # exactly the queries that need it: walking the kudos index and stopping
        # at 300 terminates almost immediately when a large share of rows match,
        # which is what "broad" means. It is NOT done for narrow queries, where
        # that same walk is the pathological case the ceiling exists to avoid.
        # Captured, not just tested: the same number decides whether this arm
        # runs AND how high its kudos floor should be. _query_is_category is
        # lru_cached, so this is one lookup either way.
        _breadth = _query_is_category(db, q_norm)
        if _breadth >= 100:
            # kudos > 0 is not a filter on what may appear — it narrows only
            # this extra source. Without it the planner had to bitmap the whole
            # match set and top-N sort 700,000 rows, which cost 6s on "harry
            # potter". With it, it intersects against the kudos index instead and
            # the same query is well under a second. Rows with no kudos still
            # arrive through the other two branches; this one exists purely to
            # guarantee the best-READ works are present, and a work with zero
            # recorded kudos is by definition not one of those.
            # Plain .desc(), NOT nullslast(). ix_stories_kudos_desc is
            # `btree (kudos DESC)`, and a DESC btree is NULLS FIRST — so
            # `kudos DESC NULLS LAST` does not match it and Postgres cannot walk
            # it in order. What it did instead was BitmapAnd the FTS index with
            # the kudos index over 1,696,107 rows and SORT the result, and this
            # single arm then took 38.8 SECONDS of a 32-second query on
            # `q=harry potter` — the arm that exists to make broad queries fast
            # was the entire reason they were slow.
            #
            # `kudos > 0` already excludes NULLs, so dropping nullslast changes
            # nothing about which rows come back or in what order; it only lets
            # the planner stop after 300, which is what the comment above always
            # claimed this did.
            # The kudos floor scales with how broad the query is, and it is the
            # difference between this arm costing 2 seconds and 28.
            #
            # This arm intersects the FTS match set with the kudos index and
            # sorts. For a genuinely broad query that match set is enormous —
            # `harry potter` matches 1,308,857 rows — so the intersection and
            # the heap fetches behind it dominate everything else in the query.
            # Measured on `q=harry potter`, this arm alone:
            #
            #     kudos > 0      27.9s        kudos > 500    13.0s
            #     kudos > 100    17.8s        kudos > 1000    2.3s
            #
            # It was 38.8s of a 32s query in the live plan: the arm that exists
            # to make broad queries good was the entire reason they were slow.
            #
            # A FIXED floor cannot work — 1000 would empty this arm for a
            # mid-sized pairing whose best works sit at 300 kudos. But breadth is
            # already measured: breadth is 686,558 for "harry potter", 45,960
            # for "time travel" and 134 for "dramione". The broader the query,
            # the more well-read works exist, and the higher the floor can safely
            # go.
            #
            # Crucially this arm is ADDITIVE — it guarantees the best-read works
            # are present, on top of the other arms which return everything else.
            # So a floor can only change what this arm ADDS. It can never remove
            # a match from the results, which is what makes tuning it safe.
            _kudos_floor = (1000 if _breadth >= 100_000
                            else 250 if _breadth >= 20_000
                            else 0)
            parts.append(db_query.filter(Story.kudos > _kudos_floor)
                         .order_by(Story.kudos.desc())
                         .limit(POPULAR_CANDIDATES))
        # ONE n-ary union, not a chain of pairwise ones.
        #
        # `for part in parts[1:]: merged = merged.union(part)` builds
        # ((a ∪ b) ∪ c) ∪ d, and every ∪ is a separate dedup. UNION deduplicates
        # on the whole row, and these rows are entities — so the plan carried
        # THREE stacked HashAggregates whose Group Key was all 45 columns of
        # stories, 2,019 bytes wide, right down to `search_vector` (which is
        # NULL in every row of the table and read by nothing). Measured on
        # `q=hogwarts`: 3,038ms total, of which the nested Appends and their
        # dedups were ~2,300ms.
        #
        # A single union(*rest) asks for the same set — UNION is associative and
        # duplicate-free either way — but dedups once instead of three times.
        candidates = parts[0].union(*parts[1:]).subquery() if len(parts) > 1 \
            else parts[0].subquery()
    else:
        # Plain browse (no text): db_query is a filter over the whole index.
        # An unordered LIMIT 5001 slice is an arbitrary sample of 19.7M rows, so
        # ranking "Most popular" within it ranked the wrong rows — the page was
        # a random sample's top score, not the index's, and the works everyone
        # actually knows were almost never in that sample. The partial
        # popularity index makes ordering the slice by score cheap (browse with
        # no other filter is a straight index walk), so "Most popular" now
        # ranks the true top of the index. `popularity IS NOT NULL` is both the
        # index's predicate and what nullslast() does anyway: a work with no
        # recorded engagement has no score and belongs after every scored one.
        base = db_query
        if sort == "popularity_desc":
            # `popularity IS NOT NULL` is a FILTER, and it was applied to every
            # search — so "Most popular" silently deleted every match without a
            # score. Only 550,384 of 20,075,332 works have one (2.7%), because a
            # score is a percentile among works with recorded engagement and the
            # bulk dumps carry none. So a narrow search lost nearly everything:
            # 7 works for a rare pairing under Relevance, 0 under Most popular,
            # with nothing to say they had been dropped rather than not exist.
            #
            # nullslast() ORDERS unscored works last. It does not remove them,
            # which is what the old comment here assumed.
            #
            # The filter still earns its place on an unfiltered browse, and only
            # there: that is a top-N walk of the partial popularity index over
            # 20M rows, and without the predicate it becomes a sort of the whole
            # table. Once the reader has narrowed the search the candidate set is
            # small, nullslast is cheap, and correctness is what matters.
            if _narrowed:
                base = base.order_by(Story.popularity.desc().nullslast())
            else:
                base = (base.filter(Story.popularity.isnot(None))
                           .order_by(Story.popularity.desc()))
        else:
            # EVERY other column sort has exactly the same problem, and only
            # popularity_desc had ever been fixed. Ordering after an unordered
            # LIMIT sorts an arbitrary 5,001 of the matches, so the answer is not
            # the top of the index, it is the top of a sample. Measured, on
            # `fandoms=Harry Potter&sort=updated_desc`: the page called the
            # newest work Feb 2026 when the real answer was 15 Aug 2026 — six
            # months of the freshest works in the largest fandom on the site
            # simply unreachable, with nothing to say so.
            #
            # So order BEFORE the cap, the same way. What makes that affordable
            # is one partial index per sort, mirroring ix_stories_popularity:
            # only 3.9% of works have hits, 4.7% comments, 4.7% bookmarks, so
            # those indexes are tens of MB rather than hundreds. The predicate
            # goes on for an UNFILTERED browse only, for the reason spelled out
            # above for popularity: as a filter it would delete matches, and
            # "Most hits" over a narrowed search must still return works with
            # no recorded hits rather than nothing at all.
            if _narrowed:
                # Narrowed searches deliberately keep the OLD cap-then-order
                # behaviour, and that is a known inaccuracy, not an oversight.
                #
                # Ordering before the cap is just as correct here, but there is
                # no index that answers "newest works tagged Fluff": Postgres
                # has to bitmap every match and top-N sort it. Measured on this
                # database, ordered before the cap: Naruto 7.9s, Harry Potter
                # 18.9s, tags=Fluff 33.9s — and bounding it with a date window
                # moved the cost into the count rather than removing it. A page
                # that takes twenty seconds is a worse answer than an imprecise
                # one, so this waits for a precomputed per-fandom recent list
                # (the shape fandom_hubs already uses) rather than being paid
                # for on every filtered search. See CLAUDE.md.
                pass
            elif sort in _BROWSE_ORDER:
                pred, order_by = _BROWSE_ORDER[sort]
                base = base.filter(pred(Story)).order_by(order_by(Story))
            elif sort in _BROWSE_ORDER_DIRECT:
                base = base.order_by(_sort_expr(Story, sort))
        candidates = base.limit(COUNT_CEILING + 1).subquery()
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

        # Is this the name of a thing, or the name of a subject?
        facet_hits = _query_is_category(db, q_norm)
        is_category = facet_hits >= 100

        # Weights shift with that answer rather than there being two code paths.
        # A category query still rewards a matching title; it just stops letting
        # a title outrank everything else on its own.
        # A category query leans on readership and subject match; a title query
        # leans on the title. Same formula, different emphasis.
        # The category weights were still too kind to the title, and the failure
        # was consistent: searching a TROPE returned tiny works NAMED after it
        # above the works actually about it. "coffee shop au" gave a 810-word fic
        # called "Coffee Shop AU" (156 kudos) above one with 15,097; "time travel"
        # gave a 1,128-word fic called "time travel" (320 kudos) above works with
        # 35,544 and 48,099.
        #
        # Three separate terms were all rewarding the same coincidence at once,
        # which is why it compounded: an exactly-matching title collects
        # exact_bonus AND a ~1.0 trigram title_sim AND an inflated ts_rank,
        # because ts_rank normalisation divides by document length and these
        # works have almost no document. Together that outweighed a 100x
        # readership difference.
        #
        # So for a query that IS a category, the title nearly stops voting. It is
        # not zeroed — "Dramione" sits at only 134 facet hits and a work of that
        # name should still place if it is read — but it can no longer carry a
        # work on its own. A title query is untouched: `all the young dudes` is 13
        # facet hits, is not a category, and still puts the 318,463-kudos work
        # first.
        w_title = 0.35 if is_category else 3.0
        w_exact = 0.15 if is_category else 4.0
        w_pop = 3.5 if is_category else 1.0
        w_text = 2.5 if is_category else 1.5

        # A bonus, not a hard tier. As a tier, ANY work whose title contained the
        # words beat every better overall match, and popularity never got a vote
        # — which is how a 0-kudos fic called "Harry Potter" ended up above all
        # 686,000 actual Harry Potter stories.
        exact_bonus = case((title_l == q_norm, w_exact),
                           (title_l.like(q_norm + "%"), w_exact * 0.4),
                           else_=0.0)

        # Log-damped and normalised to 0..1. Raw kudos would swamp everything:
        # 318,436 against 1,000 is not a 318x better answer, and ln makes it 2x.
        pop = func.least(
            func.ln(1 + func.coalesce(S.kudos, 0) + func.coalesce(S.hits, 0) / 20.0)
            / 13.8, 1.0)
        title_sim = func.similarity(func.coalesce(S.title, ""), q_norm)
        # Normalisation flag 1 divides the rank by 1 + log(document length).
        # Without it ts_rank is raw term frequency, so a work whose entire
        # indexed document is the two words you searched for scored higher than
        # one that is genuinely about the subject and says so across a summary
        # and thirty tags. That is how "Dramione" (the title) outranked every
        # story actually tagged Dramione.
        text_rank = func.ts_rank(_story_tsv(S),
                                 func.websearch_to_tsquery(_REGCONFIG, q), 1)

        relevance = (w_title * title_sim + exact_bonus
                     + w_text * text_rank + w_pop * pop)

        ordered = ordered.order_by(
            relevance.desc(),
            S.word_count.desc().nullslast(),
        )
    else:
        # Nothing to rank against. word_count is the only signal populated for
        # essentially every row (99.9%), so it beats an all-ties kudos sort.
        ordered = ordered.order_by(S.word_count.desc().nullslast())

    offset  = (page - 1) * per_page

    # ── Fast path: walk the sort's index instead of sorting the candidate set ──
    #
    # A filtered browse ordered by date used to cost 7.9s (Naruto), 18.9s (Harry
    # Potter) and 33.9s (tags=Fluff), because the only way to order the capped
    # candidate set is to collect every match and top-N sort it. The page does
    # not need every match. It needs `offset + per_page` rows — twenty of them —
    # and ix_stories_last_activity is already in exactly that order.
    #
    # Two things have to be true before Postgres will walk it and stop early:
    #
    #   1. the ORDER BY must match the index exactly. It is
    #      `COALESCE(updated_at, published_at) DESC NULLS LAST`, which is what
    #      _sort_expr produces. Dropping NULLS LAST costs 6.6s.
    #   2. there must be a RANGE BOUND on the sort expression. This is the
    #      unobvious half: `>= '-infinity'` selects exactly the rows that
    #      `IS NOT NULL` selects — no timestamp is below -infinity — but only the
    #      range form gives the scan a start position in the btree. Measured on
    #      Harry Potter, same rows, same order:
    #
    #          IS NOT NULL      6,649 ms
    #          >= '-infinity'       0.17 ms
    #
    # The bound is why this is a fast path and not a rewrite: it changes the
    # PLAN, not the result set.
    #
    # It applies only to a small limit. The same walk at LIMIT 5001 costs 3.5s
    # (Harry Potter) and 42.9s (Naruto), because the ceiling is far enough down
    # the index to lose the early exit — so the count keeps its own cheap query
    # over the unordered capped set (~0.17s) rather than riding along on the
    # rows via count(*) OVER ().
    _ceiling = COUNT_CEILING
    _fast_rows = None
    if (not q) and _narrowed and sort in _FAST_SORTS and offset + per_page <= COUNT_CEILING:
        try:
            _bound, _order = _FAST_SORTS[sort]
            _fast_rows = (db_query
                          .filter(_bound(Story))
                          .order_by(_order(Story))
                          .offset(offset).limit(per_page).all())
        except Exception:
            log.warning("ordered fast path failed; falling back", exc_info=True)
            _fast_rows = None

    if _fast_rows is not None:
        rows = [(r, None) for r in _fast_rows]
        # Count over an ID-ONLY capped subquery. `candidates` selects all 45
        # columns because the rows are read from it on the normal path; counting
        # them makes the executor materialise thousands of ~1.3KB rows to throw
        # the contents away.
        #
        # Cached separately from the response, and keyed on the FILTERS ALONE.
        # Once the rows became an index walk this count was the entire remaining
        # cost of a filtered browse — measured at 14,152ms of a 14,226ms request
        # — and it is the same number for every sort and every page of the same
        # search. So changing the sort, or paging, re-derived a total that could
        # not have changed, which is exactly the "changing the sort takes ages"
        # complaint. The response cache cannot help: sort and page are part of
        # its key, so every one of them is a miss.
        #
        # The key is a hash of the count query's own compiled SQL. That is exact
        # by construction — identical filters produce identical SQL, and sort and
        # page do not appear in this query at all, so they cannot fragment it.
        _count_q = db.query(func.count()).select_from(
            db_query.with_entities(Story.id)
                    .limit(SEARCH_BROWSE_COUNT_CEILING + 1).subquery())
        _ceiling = SEARCH_BROWSE_COUNT_CEILING
        total = None
        try:
            _sig = str(_count_q.statement.compile(
                db.bind, compile_kwargs={"literal_binds": True}))
            _ckey = "count|" + hashlib.sha1(_sig.encode()).hexdigest()
            _hit = shared_get(db, _ckey)
            if _hit is not None:
                total = int(_hit)
        except Exception:
            _ckey = None
            log.debug("count cache key failed", exc_info=True)

        if total is None:
            total = _count_q.scalar() or 0
            if _ckey:
                # Longer than the response TTL: the number is capped anyway, so
                # it is coarse, and it is the expensive half.
                shared_put(db, _ckey, str(total), ttl=COUNT_CACHE_TTL)
    elif offset >= COUNT_CEILING:
        # Beyond the candidate ceiling there is nothing to fetch, so do not ask.
        #
        # Skipping only the COUNT was not enough: the rows query still had to
        # build the entire candidate set — for a text query, the whole UNION —
        # before OFFSET could throw all of it away. `q=harry&page=99999` still
        # took 92 seconds to return zero rows it could have known were zero.
        # OFFSET is not a way to avoid work in Postgres; it is a way to do the
        # work and discard it.
        rows = []
    else:
        rows = ordered.offset(offset).limit(per_page).all()
    # count(*) OVER () repeats the same total on every row; it is only absent
    # when the page is empty, which also means there was nothing to count.
    if _fast_rows is not None:
        pass
    elif rows:
        total = int(rows[0][1])
    elif page > 1 and offset >= _ceiling:
        # Past the candidate ceiling, so the page is empty BY CONSTRUCTION and
        # the total is the capped one. No query can tell us anything more.
        #
        # This is the cheap answer to a case that was catastrophically expensive:
        # the fallback below re-counts the whole candidate set, which for a text
        # query means re-running the entire UNION. Measured on `q=harry&page=500`
        # — an offset of 10,000 against a 5,001-row ceiling, so a page that
        # cannot contain anything — 57.9 SECONDS, which on the public tier is
        # past the proxy timeout and surfaced as a 500. A crawler walking
        # pagination links, or anyone who edits the page number in the URL, could
        # trip it.
        total = _ceiling
    elif page > 1:
        # An empty page past the first, but still WITHIN the ceiling, genuinely
        # tells us nothing about the total — the window count only rides along on
        # rows that came back. Paging beyond the end would otherwise report
        # "0 results" for a search that has thousands, so count for this case.
        # Bounded, because the candidate set it counts is bounded.
        total = db.query(func.count()).select_from(candidates).scalar() or 0
    else:
        total = 0
    count_is_capped = total > _ceiling
    if count_is_capped:
        total = _ceiling
    indexed = [r[0] for r in rows]

    # How many matches the explicit filter is hiding.
    #
    # "No stories matched" is the wrong sentence when the truth is "your content
    # setting hid all of them". The browse hubs rank by kudos with no rating
    # filter, so an explicit work can sit at the top of a ship page — clicking
    # its author then produced an empty search that read as "we do not have
    # them". Reported against Kill Your Darlings by MesserMoon (32,908 kudos,
    # top of remus-lupin-sirius-black, rated E).
    #
    # Page 1 only, whenever the filter is on — NOT only when the page came back
    # empty. The first version gated on `total == 0` and missed the case that
    # was actually reported: MesserMoon has ten works indexed, one of them
    # rated NR and nine rated E, so the author link returned a single result
    # rather than none. There was nothing to notice, which is worse than an
    # empty page — the reader concludes the index has one work by that author.
    #
    # Only when the reader is looking at their WHOLE result set — `total` fits
    # on this page. That is both the cheap case and the only one where the
    # number changes anything: someone seeing 1 of MesserMoon's 10 works
    # concludes the index holds one, while someone looking at 5,000 Naruto
    # results does not need to be told 999+ more exist.
    #
    # It is also what keeps this off the hot path. Measured on a broad filtered
    # search, the count added 0.30s -> 1.11s, because it scans until it has
    # found HIDDEN_EXPLICIT_CEILING explicit rows. On a search narrow enough to
    # return a single page it is unmeasurable (0.01s), since the same filters
    # that returned one row return few explicit ones.
    #
    # Rebuilt from the same `filters` minus the one predicate, so every other
    # filter the reader set still applies and the number is about the rating and
    # nothing else. In the text-query path the real candidate set is a UNION
    # with a fuzzy-title arm this does not reproduce, so it can undercount a
    # misspelled title — it cannot overcount, which is the direction that would
    # promise results that are not there.
    hidden_explicit = 0
    if _explicit_pred is not None and page == 1 and total <= per_page:
        try:
            # Both expressions of "hide explicit" have to come out, or the count
            # contradicts itself. See the note on _ratings_pred above.
            dropped = {id(_explicit_pred)}
            if _ratings_is_all_but_explicit and _ratings_pred is not None:
                dropped.add(id(_ratings_pred))
            others = [f for f in filters if id(f) not in dropped]
            capped = (db.query(Story.id)
                        .filter(*others, Story.rating == RatingEnum.explicit)
                        .limit(HIDDEN_EXPLICIT_CEILING + 1).subquery())
            hidden_explicit = db.query(func.count()).select_from(capped).scalar() or 0
        except Exception:
            # A count for a hint must never be the reason a search fails.
            log.debug("hidden-explicit count failed", exc_info=True)
            hidden_explicit = 0

    # Per-archive split of the matches.
    #
    # Cheap by construction rather than by luck: `candidates` is already bounded
    # — COUNT_CEILING + 1 rows in the plain case, and a union of individually
    # limited parts in the text case — so this aggregates at most ~5k rows that
    # Postgres has just materialised for the main query. Measured at well under
    # the noise floor of a search.
    #
    # Skipped entirely when only one archive is in play: the answer is then just
    # `total`, and the extra round trip would buy nothing.
    site_counts: Dict[str, int] = {}
    # Only when the count is exact.
    #
    # `total` is capped at COUNT_CEILING, but the candidate set behind it is not
    # exactly that size — in the text path it is a union of separately limited
    # parts, so a search reporting "5,000+" was really counting 5,400. A
    # breakdown over that set therefore summed to 5,400 under a headline of
    # 5,000, which reads as an arithmetic error and undermines the very thing the
    # figure is there to demonstrate.
    #
    # Worse, the split of that set is not a fair sample: the union pulls
    # candidates by relevance AND by kudos, and kudos are recorded far more often
    # on AO3, so even quoting it as a percentage would overstate AO3's share.
    #
    # So the breakdown is shown only for searches whose total is exact. That is
    # also when it is most useful: a narrow search returning 40 works across two
    # archives is a far better demonstration of what this site does than "5,000+"
    # ever was.
    if len(site_enums) > 1 and total > 0 and not count_is_capped:
        try:
            # Through `S`, the ORM alias over `candidates`, not through
            # `candidates.c.site`: in the text-query path `candidates` is a UNION
            # subquery, and SQLAlchemy relabels a union's columns
            # (`stories_site`), so addressing them by their table name raised
            # AttributeError. The alias maps the entity whatever the labelling.
            # `s.value`, not `str(s)`: these come back as SiteEnum members and
            # str() on an enum yields "SiteEnum.ao3", which the frontend's site
            # label map does not know and would render raw.
            site_counts = {
                (s.value if hasattr(s, "value") else str(s)): int(n)
                for s, n in db.query(S.site, func.count()).group_by(S.site).all()
                if s
            }
        except Exception as e:
            # A breakdown is a nicety; a search is not. Never fail the request
            # for it — but say so, rather than leaving a silent empty dict that
            # looks identical to "every match came from one archive".
            log.warning("site_counts failed: %s: %s", type(e).__name__, e)
            site_counts = {}

    # Series, looked up only for the rows actually being returned.
    #
    # A join in the main query would be paid for all 5,001 candidates and for
    # the overwhelming majority of works, which are in no series at all. Twenty
    # ids against an indexed table costs nothing and answers the same question.
    def _attach_series(objs) -> None:
        ids = [str(o.id) for o in objs if getattr(o, "id", None)]
        if not ids:
            return
        rows = db.execute(sql_text("""
            SELECT sw.story_id, s.id, s.name, sw.position
            FROM series_works sw JOIN series s ON s.id = sw.series_id
            WHERE sw.story_id = ANY(CAST(:ids AS uuid[]))
            ORDER BY s.confidence DESC
        """), {"ids": ids}).fetchall()
        seen: dict = {}
        for story_id, sid, name, pos in rows:
            seen.setdefault(str(story_id), (str(sid), name, pos))
        for o in objs:
            hit = seen.get(str(getattr(o, "id", "")))
            if hit:
                o._series_id, o._series_name, o._series_position = hit

    try:
        _attach_series(indexed)
    except Exception as e:
        log.debug(f"series attach failed: {type(e).__name__}")

    # One cached set for the whole page of results, rather than a lookup per row.
    _verified = author_permission.verified_authors(db)
    indexed_cards = [_to_card(s, _verified) for s in indexed]

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

    _response = SearchResponse(
        total=total,                          # stable across pages — indexed count only
        count_is_capped=count_is_capped,
        site_counts=site_counts,
        page=page,
        per_page=per_page,
        results=merged,
        sites_searched=[s.value for s in site_enums],
        live_count=len(live_cards),
        hidden_explicit=hidden_explicit,
        parsed_tokens=[ParsedToken(**t) for t in parsed_tokens],
    )
    if _ck is not None:
        CACHE.put(_ck, _response)
        # Share it, so the other three workers never recompute this one. Skipped
        # when live results are mixed in: those are fetched per request and are
        # the freshest thing in the response, and freezing them into a shared
        # entry would hand another reader a stale copy of something whose whole
        # point is that it is live.
        if not live_cards:
            shared_put(db, _ck, _response.model_dump_json(), SEARCH_CACHE_SECONDS)

    # Let a shared cache in front of us do the work the per-worker cache can
    # only do four times over.
    #
    # The in-process cache (search_cache.py) lives in one uvicorn worker, so with
    # four workers the same query is computed up to four times before it is warm
    # everywhere, and nothing is shared between users at all. An HTTP cache in
    # front — Cloudflare, in the deployment this is heading for — is ONE cache
    # shared by every visitor, which is the same Zipf-shaped win multiplied by
    # the whole audience instead of by one process.
    _note_total(request, _response)
    _apply_cache_headers(response, viewer)
    return _response


@router.get("/random", response_model=List[StoryCard])
def random_stories(
    count: int = Query(3, ge=1, le=12),
    fandom: Optional[str] = Query(None),
    min_words: Optional[int] = Query(None, ge=0),
    explicit: bool = Query(False),
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
    if not explicit:
        where.append("(rating <> 'explicit' OR rating IS NULL)")
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
            return [_to_card(s, author_permission.verified_authors(db)) for s in rows]

    # Fallback: whole-table scan. Only reached for filters too rare to show up in a
    # 3% page sample, where correctness matters more than the latency.
    q = db.query(Story).filter(Story.word_count > min_w,
                               Story.delisted_at.is_(None))
    if not explicit:
        q = q.filter(or_(Story.rating != RatingEnum.explicit, Story.rating.is_(None)))
    if fandom_pat:
        q = q.filter(_arr_text(Story.fandoms).ilike(fandom_pat))
    return [_to_card(s, author_permission.verified_authors(db))
            for s in q.order_by(func.random()).limit(count).all()]


# ── Serialisers ───────────────────────────────────────────────────────────────

def _to_card(s: Story, verified: set | None = None) -> StoryCard:
    return StoryCard(
        author_verified=bool(
            verified and (getattr(s.site, "value", s.site),
                          (s.author or "").strip().lower()) in verified),
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
        series_name=getattr(s, "_series_name", None),
        series_id=getattr(s, "_series_id", None),
        series_position=getattr(s, "_series_position", None),
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
