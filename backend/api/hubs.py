"""Hub pages — the crawlable way into the index.

Two kinds, served identically: one per fandom (fandom_hubs) and one per romantic
pairing (ship_hubs). Read-only and cheap by construction: both tables hold
precomputed rows, so a hub is a primary-key lookup plus a fetch of ~150 stories
by id. Nothing here ranks or scans, because these are the routes a crawler hits
hardest.

See fandom_hubs.py for why hubs exist at all, and ship_hubs.py for why pairings
get their own set rather than being a filter on a fandom hub.
"""
from __future__ import annotations

import os

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.search import (_ADULT_TAGS, _ADULT_WARNINGS,
                        _UNDERAGE_TAGS, _UNDERAGE_WARNINGS)
from db.session import get_db

import logging

log = logging.getLogger("hubs")

router = APIRouter()
ships_router = APIRouter()


class HubSummary(BaseModel):
    slug: str
    name: str
    work_count: int
    # Ships only. Shown on the index so the A-Z is browsable by the name people
    # know the pairing by, not only by the canonical tag.
    nicknames: list[str] = []
    # When the hub's CONTENTS last changed (not when it was last rebuilt) — the
    # sitemap's <lastmod>. See the column note in init_db.py.
    content_at: Optional[str] = None


class HubWork(BaseModel):
    id: str
    title: str
    author: Optional[str] = None
    summary: Optional[str] = None
    word_count: Optional[int] = None
    chapter_count: Optional[int] = None
    kudos: Optional[int] = None
    site: Optional[str] = None
    complete: Optional[bool] = None


class RecommendedWork(BaseModel):
    """A work this fandom has been recommending to each other lately.

    A different measurement from every other list on this page. `works` is
    ranked by popularity — a percentile of kudos, bookmarks and hits, which
    counts READERSHIP. This counts how many distinct people told somebody else
    to read it, in a window, which is the thing an archive cannot know about
    itself and the reason the whole table exists.
    """
    id: str
    title: str
    author: Optional[str] = None
    summary: Optional[str] = None
    site: Optional[str] = None
    word_count: Optional[int] = None
    complete: Optional[bool] = None
    # DISTINCT PEOPLE, never mentions. One reader naming a favourite in every
    # fortnightly thread is one reader — counting mentions would let the most
    # talkative person in a subreddit decide what a fandom is reading.
    people: int
    last_at: Optional[str] = None
    # Where it was said, so the page can credit the community rather than
    # presenting a subreddit's opinion as the site's own.
    communities: list[str] = []


class SiteSection(BaseModel):
    """One archive's top works. Ranked within the site — see fandom_hubs.py for
    why a single cross-archive ranking could only ever return AO3."""
    site: str
    works: list[HubWork]
    # How many works this archive actually holds for the hub, which is a
    # different number from `len(works)` and the one that matters. The listing
    # is capped per archive, so it says only "this archive filled its quota" —
    # and ordering the page by it tied every large hub and broke the tie on
    # dictionary order. See the note in hub_build.py for what that did to the
    # Drarry page. 0 on a hub built before the column existed.
    total: int = 0


class RelatedHub(BaseModel):
    """Another hub worth a link from this one. `kind` is "fandom" or "ship"."""
    kind: str
    slug: str
    name: str
    work_count: int


class TopicLink(BaseModel):
    slug: str          # "harry-potter/time-travel"
    tag: str
    work_count: int
    content_at: str | None = None   # the sitemap's <lastmod>


class HubDetail(BaseModel):
    slug: str
    name: str
    work_count: int
    # Fandom names for a pairing ("Drarry"), primary first. Ships only; empty
    # for fandoms and for the many pairings that have no portmanteau. See
    # SHIP_NICKNAMES in ship_hubs.py for why these are curated, not derived.
    nicknames: list[str] = []
    # Flat, interleaved across archives. Kept for anything reading the old shape.
    works: list[HubWork]
    sections: list[SiteSection]
    # Hubs worth walking to from here. See `_related` — this is the site's only
    # lateral link, and until it existed every hub was a crawl dead end
    # sideways: the index linked 11,190 hubs, each hub linked 100 story pages,
    # and no hub linked to any other.
    related: list[RelatedHub] = []
    # Trope pages under this fandom (topic_hubs.py): on a fandom hub, its
    # tropes; on a trope hub, the fandom's OTHER tropes.
    topics: list[TopicLink] = []
    # Set on a trope hub only.
    fandom_slug: str | None = None
    fandom_name: str | None = None
    tag: str | None = None
    # Empty until the harvest has read a thread for this fandom, which is the
    # normal state for most of 5,025 hubs — the section simply does not render.
    recommended: list[RecommendedWork] = []
    # The window the counts describe, so the page can say "in the last 30 days"
    # rather than leaving a number to be read as all-time.
    recommended_days: int = 0
    # The AO3 tag whose rec bookmarks were read for this fandom, so the hub can
    # link straight to them. None when this fandom's bookmarks were not read
    # (its AO3 recommendations then came via a crossover's fandom).
    recommended_ao3_tag: Optional[str] = None


# Both hub tables have the same shape, so listing and detail differ only in the
# table name. It is interpolated, so it is checked against a literal allowlist
# rather than trusted — these are module constants today and this keeps that
# safe if a caller ever passes something derived from a request.
_TABLES = {"fandom": "fandom_hubs", "ship": "ship_hubs"}

CACHE = "public, max-age=3600, stale-while-revalidate=86400"


def _list(kind: str, response: Response, limit: int, offset: int, db: Session):
    """Every hub of one kind, largest first. Backs the index pages and the
    sitemap.

    Fully public and rebuilt offline, so a shared cache can hold it for a long
    time — this is the request a crawler makes before walking every hub."""
    table = _TABLES[kind]
    response.headers["Cache-Control"] = CACHE
    rows = db.execute(text(f"""
        SELECT slug, name, work_count, content_at FROM {table}
         ORDER BY work_count DESC, slug
         LIMIT :lim OFFSET :off
    """), {"lim": limit, "off": offset}).fetchall()
    nick = {}
    if kind == "ship":
        from ship_hubs import nicknames_for
        nick = {r[0]: nicknames_for(r[0]) for r in rows}
    return [HubSummary(slug=r[0], name=r[1], work_count=r[2],
                       nicknames=nick.get(r[0], []),
                       content_at=r[3].isoformat() if r[3] else None)
            for r in rows]


# How many lateral links a hub offers. Enough to be a real path for a crawler
# and a real choice for a reader; few enough that the page is still about the
# thing it is about.
RELATED_CAP = 8


# How far back "lately" reaches, and how many works the block shows.
#
# 30 days because the source threads are FORTNIGHTLY: a shorter window can
# catch only one of them and would make the block appear and vanish with the
# posting schedule rather than with what people are reading.
RECS_DAYS = int(os.getenv("HUB_RECS_DAYS", "30"))
RECS_LIMIT = int(os.getenv("HUB_RECS_LIMIT", "6"))


# How many DIFFERENT people must have recommended a work before it is "what this
# fandom is recommending". It was one: any single mention qualified, so the
# block was mostly one person's list. Measured 2026-10-04 over 30 days: 54
# works mentioned, every one by exactly one person.
#
# It scales with the fandom's own recommendation ACTIVITY — distinct people
# recommending anything in it in the window — not with its size in the index:
# a fandom can hold 100,000 works and have three people talking about it this
# month. Two agreeing people mean something in a quiet fandom; in a busy one
# two is noise, so the bar rises.
RECS_TIERS = ((30, 2), (100, 3), (300, 4))
RECS_TOP_PEOPLE = 5
# A quiet fandom gets a longer window before the block is given up on. The
# block states its window, so a reader always knows which it is.
RECS_WIDE_DAYS = int(os.getenv("HUB_RECS_WIDE_DAYS", "90"))
RECS_MIN_WORKS = 3


def min_people_for(activity: int) -> int:
    for ceiling, need in RECS_TIERS:
        if activity < ceiling:
            return need
    return RECS_TOP_PEOPLE


_RECS_GATED = """
           AND s.fandoms && CAST(:variants AS text[])
           AND NOT s.gate_underage
           AND NOT s.gate_adult
           AND NOT (COALESCE(s.warnings,'{}') && CAST(:gate_uw AS text[]))
           AND NOT (COALESCE(s.tags,'{}')     && CAST(:gate_ut AS text[]))
           AND NOT (COALESCE(s.warnings,'{}') && CAST(:gate_aw AS text[]))
           AND NOT (COALESCE(s.tags,'{}')     && CAST(:gate_at AS text[]))
"""


def _recommended_in(db, variants: list[str], days: int, limit: int) -> list["RecommendedWork"]:
    params = {"days": days, "variants": variants, "limit": limit,
              "gate_uw": _UNDERAGE_WARNINGS, "gate_ut": _UNDERAGE_TAGS,
              "gate_aw": _ADULT_WARNINGS,    "gate_at": _ADULT_TAGS}
    activity = db.execute(text(f"""
        SELECT count(DISTINCT m.recommender)
          FROM rec_mentions m JOIN stories s ON s.id = m.story_id
         WHERE m.mentioned_at > now() - make_interval(days => :days)
        {_RECS_GATED}
    """), params).scalar() or 0
    params["need"] = min_people_for(int(activity))
    rows = db.execute(text(f"""
        SELECT s.id, s.title, s.author, s.summary, s.site, s.word_count,
               s.status,
               count(DISTINCT m.recommender) AS people,
               max(m.mentioned_at)           AS last_at,
               array_agg(DISTINCT m.community) AS communities
          FROM rec_mentions m
          JOIN stories s ON s.id = m.story_id
         WHERE m.mentioned_at > now() - make_interval(days => :days)
        {_RECS_GATED}
         GROUP BY s.id, s.title, s.author, s.summary, s.site, s.word_count, s.status
        HAVING count(DISTINCT m.recommender) >= :need
         -- Most people first, then most recent. Recency breaks the tie rather
         -- than leading, or a single mention today would outrank four last
         -- week and the block would rewrite itself every day.
         ORDER BY people DESC, last_at DESC
         LIMIT :limit
    """), params).fetchall()
    return [
        RecommendedWork(
            id=str(r[0]), title=r[1], author=r[2], summary=r[3], site=r[4],
            word_count=r[5],
            complete=(str(r[6]).lower() in ("complete", "completed")
                      if r[6] is not None else None),
            people=int(r[7] or 0),
            last_at=r[8].isoformat() if r[8] else None,
            communities=[c for c in (r[9] or []) if c],
        )
        for r in rows
    ]


def _recommended(db, variants: list[str], limit: int = RECS_LIMIT
                 ) -> tuple[list["RecommendedWork"], int]:
    """What this fandom has been recommending lately -> (works, window in days).

    THE CONTENT GATES APPLY IN FULL, and this is the third surface to need
    that argument written out: a hub has no Explicit toggle, because it is a
    static page a search engine hands to a stranger, so the safe default is the
    only setting it has. Both the indexed columns AND the arrays — the columns
    are a cache maintained by a trigger, and a column that defaults to `false`
    reads as safe.

    A work needs `min_people_for(activity)` different recommenders. RECS_DAYS
    first; a quiet fandom with fewer than RECS_MIN_WORKS qualifying works gets
    RECS_WIDE_DAYS before the block is dropped. Cheap: the candidate set is
    what a handful of threads mentioned, tens of rows.
    """
    works = _recommended_in(db, variants, RECS_DAYS, limit)
    if len(works) >= RECS_MIN_WORKS:
        return works, RECS_DAYS
    wide = _recommended_in(db, variants, RECS_WIDE_DAYS, limit)
    if len(wide) >= RECS_MIN_WORKS:
        return wide, RECS_WIDE_DAYS
    # Not enough agreement in either window: say nothing rather than present
    # one person's list as the fandom's.
    return [], 0


def _related(db, kind: str, slug: str, name: str,
             fandoms: list[str], rels: list[str]) -> list[RelatedHub]:
    """Hubs worth linking to from this one.

    Until this existed the site was two levels deep and had no sideways edges:
    `/ships` linked all 6,165 ship hubs, each ship hub linked 100 story pages,
    and no hub linked to any other hub. Measured consequence — Googlebot, which
    crawls this site 119 times a day, had reached 90 DISTINCT hubs in the whole
    of the retained log. A crawler that lands on one pairing from a search
    result has nowhere to go but back out, and no authority flows between the
    pages that actually earn traffic (56% of all referred visits land on a ship
    hub).

    Derived at read time from the works already loaded for the page, so there
    is no new column, no rebuild, and nothing to fall out of date. `variants`
    holds every archive spelling of a hub's subject, which is what lets a raw
    fandom or relationship string off a work be matched back to its hub.

    Never raises: a hub page that renders without its related links is a page,
    and one that 500s is not.
    """
    def _modal(values: list[str], top: int) -> list[str]:
        counts: dict[str, int] = {}
        for v in values:
            if v:
                counts[v] = counts.get(v, 0) + 1
        return [v for v, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]

    out: list[RelatedHub] = []
    seen = {slug}
    try:
        if kind == "ship":
            # The fandom this pairing lives in. One link, and the most valuable
            # one: it is the only edge from a niche pairing up into a page with
            # standing.
            for f in _modal(fandoms, 2):
                row = db.execute(text(
                    "SELECT slug, name, work_count FROM fandom_hubs "
                    " WHERE :v = ANY(variants) ORDER BY work_count DESC LIMIT 1"
                ), {"v": f}).fetchone()
                if row and row[0] not in seen:
                    seen.add(row[0])
                    out.append(RelatedHub(kind="fandom", slug=row[0],
                                          name=row[1], work_count=row[2]))
                    break
            # Other pairings for the same characters. "If you read Castiel/Dean,
            # here is everything else either of them is shipped with" is a real
            # reader question, and it is the edge that connects the long tail of
            # pairings to each other rather than only to the A-Z index.
            halves = [h.strip() for h in name.split("/") if len(h.strip()) > 2]
            # Taken a slice at a time from EACH half in turn, not all of one
            # then all of the other. Ordering by work_count across a single
            # query filled the whole cap with "Castiel/..." and never reached
            # Dean, which answers half the question a reader came with.
            per_half = [db.execute(text(
                "SELECT slug, name, work_count FROM ship_hubs "
                " WHERE name ILIKE :pat AND slug <> :self"
                " ORDER BY work_count DESC LIMIT :lim"
            ), {"pat": f"%{half}%", "self": slug,
                "lim": RELATED_CAP}).fetchall() for half in halves]
            for i in range(RELATED_CAP):
                for rows in per_half:
                    if len(out) >= RELATED_CAP or i >= len(rows):
                        continue
                    r = rows[i]
                    if r[0] not in seen:
                        seen.add(r[0])
                        out.append(RelatedHub(kind="ship", slug=r[0],
                                              name=r[1], work_count=r[2]))
        else:
            # A fandom's most-written pairings. The fandom hubs are the ones
            # that cannot outrank AO3 for their own name, so their job is to
            # pass a crawler on to the ship hubs, which can.
            for rel in _modal(rels, RELATED_CAP * 2):
                if len(out) >= RELATED_CAP:
                    break
                row = db.execute(text(
                    "SELECT slug, name, work_count FROM ship_hubs "
                    " WHERE :v = ANY(variants) ORDER BY work_count DESC LIMIT 1"
                ), {"v": rel}).fetchone()
                if row and row[0] not in seen:
                    seen.add(row[0])
                    out.append(RelatedHub(kind="ship", slug=row[0],
                                          name=row[1], work_count=row[2]))
    except Exception:
        db.rollback()
        return []
    return out[:RELATED_CAP]


def _works_and_sections(db: Session, top_ids, top_by_site, site_counts):
    """A hub's works, re-gated at read time, and its per-archive sections.

    Shared by fandom, pairing and trope hubs so the gating below exists once.
    """
    ids = list(top_ids or [])
    works: list[HubWork] = []
    _fandoms: list[str] = []
    _rels: list[str] = []
    if ids:
        # Re-checking delisted/restricted at read time rather than trusting the
        # snapshot: a hub may be hours or days old, and a work withdrawn since
        # the build must not keep appearing on an indexed page. WITH ORDINALITY
        # preserves the precomputed ranking without re-sorting by kudos here.
        rows = db.execute(text("""
            SELECT s.id, s.title, s.author, s.summary, s.word_count,
                   s.chapter_count, s.kudos, s.site, s.status,
                   -- Only for _related below. Free here: these rows are
                   -- already being read, and the alternative is a second pass
                   -- over the same works.
                   s.fandoms, s.relationships
              FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS t(id, ord)
              JOIN stories s ON s.id = t.id
             WHERE s.delisted_at IS NULL
               AND s.source_restricted_at IS NULL
               -- Sexualised minors, excluded here as everywhere. Hub pages are
               -- the crawlable surface and the pages search engines send
               -- strangers to, so they are the LAST place this should appear.
               -- See _UNDERAGE_TAGS in api/search.py for why the tag list is
               -- exact rather than a pattern.
               -- Content gates, both tiers. A hub has no Explicit toggle:
               -- it is a static page a search engine hands to a stranger, so
               -- the safe default is the ONLY setting it has.
               --
               -- COLUMNS AND ARRAYS, and the arrays are not optional here.
               -- The indexed booleans are set by a database trigger, so they
               -- are right for everything written since the trigger existed
               -- and only as right as the BACKFILL for everything older — and
               -- a column that defaults to `false` reads as safe. Measured
               -- mid-backfill on this exact query: **9 of 141 works** on the
               -- Harry Potter hub and 10 of 93 on a ship hub carried adult
               -- tier terms and were not yet flagged, on the one surface with
               -- no toggle at all.
               --
               -- The note that used to sit here said a negated array
               -- containment "was not affordable". That was true of the
               -- SEARCH path, where the predicate spans 20.5M rows, and was
               -- carried over to a query bounded by the hub's own `top_ids`
               -- — a few hundred rows already fetched by primary key, where
               -- it costs nothing. Measured: 54.9ms without, **4.9ms with**.
               -- Check which query a cost applies to before inheriting it.
               AND NOT s.gate_underage
               AND NOT s.gate_adult
               AND NOT (COALESCE(s.warnings,'{}') && CAST(:gate_uw AS text[]))
               AND NOT (COALESCE(s.tags,'{}')     && CAST(:gate_ut AS text[]))
               AND NOT (COALESCE(s.warnings,'{}') && CAST(:gate_aw AS text[]))
               AND NOT (COALESCE(s.tags,'{}')     && CAST(:gate_at AS text[]))
             ORDER BY t.ord
        """), {"ids": ids,
               "gate_uw": _UNDERAGE_WARNINGS, "gate_ut": _UNDERAGE_TAGS,
               "gate_aw": _ADULT_WARNINGS,    "gate_at": _ADULT_TAGS,
               }).fetchall()
        _fandoms = [f for r in rows for f in (r[9] or [])]
        _rels = [v for r in rows for v in (r[10] or [])]
        works = [
            HubWork(
                id=str(r[0]), title=r[1], author=r[2], summary=r[3],
                word_count=r[4], chapter_count=r[5], kudos=r[6], site=r[7],
                complete=(str(r[8]).lower() in ("complete", "completed")
                          if r[8] is not None else None),
            )
            for r in rows
        ]

    # Group into per-archive sections using the stored per-site ordering. Built
    # from the same `works` list, so the delisted/restricted re-check above
    # applies to the sections too rather than being done twice.
    by_id = {w.id: w for w in works}
    # Index 5, named rather than spelled inline: this row is unpacked by
    # position in four places and a column added in the middle of the SELECT
    # would silently shift every one of them.
    _counts = site_counts or {}
    sections: list[SiteSection] = []
    for site, site_ids in (top_by_site or {}).items():
        picked = [by_id[i] for i in site_ids if i in by_id]
        if picked:
            sections.append(SiteSection(site=site, works=picked,
                                        total=int(_counts.get(site, 0))))
    # THE ARCHIVE THAT ACTUALLY HOLDS THE MOST, first.
    #
    # `len(s.works)` was the old key and it is the size of the CAP, not of the
    # archive: on any hub where two archives both filled their quota it tied,
    # and the tie fell to whatever order the stored dictionary had. The Drarry
    # hub — 53,210 works, and the single page type that brings the most search
    # traffic to this site — therefore opened with FictionAlley.
    #
    # `len(s.works)` is kept as the tiebreak, for hubs built before the counts
    # existed: the ordering then is no worse than it was.
    sections.sort(key=lambda s: (-s.total, -len(s.works)))
    return works, sections, _fandoms, _rels


def _detail(kind: str, slug: str, response: Response, db: Session) -> HubDetail:
    table = _TABLES[kind]
    response.headers["Cache-Control"] = CACHE
    hub = db.execute(text(
        f"SELECT slug, name, work_count, top_ids, top_by_site, site_counts, "
        f"       variants "
        f"  FROM {table} WHERE slug = :s"
    ), {"s": slug}).fetchone()
    if not hub:
        raise HTTPException(status_code=404,
                            detail="No such fandom" if kind == "fandom"
                                   else "No such pairing")

    works, sections, _fandoms, _rels = _works_and_sections(db, hub[3], hub[4], hub[5])

    nicknames: list[str] = []
    if kind == "ship":
        from ship_hubs import nicknames_for
        nicknames = nicknames_for(hub[0])

    related = _related(db, kind, hub[0], hub[1], _fandoms, _rels)

    # What the fandom has been recommending lately. Never allowed to fail the
    # page: this is a new table fed by a rate-limited harvest, so an empty or
    # broken result must cost the block and nothing else.
    try:
        recommended, rec_days = _recommended(db, list(hub[6] or []))
    except Exception:
        log.warning("hub %s: recommended block unavailable", hub[0], exc_info=True)
        recommended, rec_days = [], 0

    ao3_tag = None
    if recommended:
        try:
            with db.begin_nested():
                ao3_tag = db.execute(text(
                    "SELECT tag FROM rec_ao3_state WHERE slug = :s"),
                    {"s": hub[0]}).scalar()
        except Exception:
            ao3_tag = None

    topics = _topics_for(db, hub[0]) if kind == "fandom" else []

    return HubDetail(slug=hub[0], name=hub[1], work_count=hub[2],
                     nicknames=nicknames, works=works, sections=sections,
                     related=related, recommended=recommended,
                     recommended_days=rec_days, recommended_ao3_tag=ao3_tag,
                     topics=topics)


def _topics_for(db: Session, fandom_slug: str, exclude: str | None = None,
                limit: int = 24) -> list[TopicLink]:
    """A fandom's trope pages, biggest first. Never fails the page."""
    try:
        rows = db.execute(text("""
            SELECT slug, tag, work_count FROM topic_hubs
             WHERE fandom_slug = :f AND slug IS DISTINCT FROM :x
             ORDER BY work_count DESC LIMIT :n
        """), {"f": fandom_slug, "x": exclude, "n": limit}).fetchall()
    except Exception:
        db.rollback()
        return []
    return [TopicLink(slug=r[0], tag=r[1], work_count=r[2]) for r in rows]


@router.get("", response_model=list[HubSummary])
def list_hubs(
    response: Response,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _list("fandom", response, limit, offset, db)


# ── the landing page's browse surface ────────────────────────────────────────
#
# One request for the front door's "somewhere to go", rather than four.
#
# The landing page asked a first-time visitor with no search in mind to pick from
# twelve fandoms, and a fic-finder site's readers overwhelmingly arrive looking
# for a CHARACTER or a PAIRING, not a universe. Asking them to name a fandom
# first is the one step this index exists to avoid. The two lists below are the
# two things `search` can filter on that are not expressible as a guess about
# the plot — and the two that have a page of their own.
#
# Read from the two hub tables rather than from `facets`, for the reason this
# file already exists: both tables are written by `hub_build.build_groups` and
# are exactly sorted by `work_count`, so this is two bounded index walks over a
# few thousand rows and no scan of `stories` at all. `facets` would have needed a
# `GROUP BY kind, value` over 1.98M rows, which is the query the watchdog note
# in this repo is about.
#
# The front door's two lists, and why a tag is not one of them.
#
# A row here is a LINK to a hub page, which is the only crawlable, bounded,
# server-rendered route into the index — robots.txt blocks the `/?q=…` search
# space, so a button that filled the query box was a dead end for a crawler and
# a worse answer for a reader than the page already on the site. A TAG has no
# page anywhere on this site, so a tag row could only ever be a search, and
# showing the same list in two different idioms on one screen read as an
# oversight rather than a choice. The tag list is therefore GONE from the front
# door, not disabled: `LANDING_LIMITS` has no tag key, so the response has no
# empty `tag: []` for the frontend to keep a tab alive on.
#
# 30 rather than 12, because twelve was a phone-screen budget applied to a
# desktop page. Both lists are ordered by `work_count DESC, slug ASC` over
# already-materialised rows, so the cost is the same.
LANDING_LIMITS = {"fandom": 30, "ship": 30}


@router.get("/landing", response_model=dict)
def landing_browse(
    response: Response,
    db: Session = Depends(get_db),
):
    """The front door's entry points. Owner-and-reader alike — this is not an
    admin surface, it is the public landing page's data, and every number here is
    already public through the hub pages it links to."""
    out: dict[str, list[dict]] = {}
    for kind, cap in LANDING_LIMITS.items():
        table = "fandom_hubs" if kind == "fandom" else "ship_hubs"
        # `slug` is selected, not just ordered by, because every row addresses a
        # hub PAGE. It is returned rather than joined into a URL server-side
        # because the frontend builds `/${kind}/${slug}`, and the kind it holds is
        # the one this loop is iterating — so a row cannot be rendered against
        # the wrong hub type.
        rows = db.execute(text(f"""
            SELECT slug, name, work_count FROM {table}
             ORDER BY work_count DESC, slug ASC
             LIMIT :lim
        """), {"lim": cap}).fetchall()
        out[kind] = [{"slug": r[0], "name": r[1], "work_count": r[2]} for r in rows]

    response.headers["Cache-Control"] = "public, max-age=600, s-maxage=3600"
    return out


@router.get("/{fandom}/t/{tag}", response_model=HubDetail)
def get_topic(fandom: str, tag: str, response: Response, db: Session = Depends(get_db)):
    """A trope hub: one fandom, one trope. See topic_hubs.py."""
    response.headers["Cache-Control"] = CACHE
    row = db.execute(text("""
        SELECT slug, name, work_count, top_ids, top_by_site, site_counts,
               fandom_slug, fandom_name, tag
          FROM topic_hubs WHERE slug = :s
    """), {"s": f"{fandom}/{tag}"}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No such page")
    works, sections, _f, _r = _works_and_sections(db, row[3], row[4], row[5])
    return HubDetail(slug=row[0], name=row[1], work_count=row[2], works=works,
                     sections=sections, topics=_topics_for(db, row[6], exclude=row[0]),
                     fandom_slug=row[6], fandom_name=row[7], tag=row[8])


@router.get("/topics/all", response_model=list[TopicLink])
def list_topics(response: Response, db: Session = Depends(get_db)):
    """Every trope page, for the sitemap."""
    response.headers["Cache-Control"] = CACHE
    rows = db.execute(text(
        "SELECT slug, tag, work_count, content_at FROM topic_hubs ORDER BY slug")).fetchall()
    return [TopicLink(slug=r[0], tag=r[1], work_count=r[2],
                      content_at=r[3].isoformat() if r[3] else None) for r in rows]


@router.get("/{slug}", response_model=HubDetail)
def get_hub(slug: str, response: Response, db: Session = Depends(get_db)):
    return _detail("fandom", slug, response, db)


@ships_router.get("", response_model=list[HubSummary])
def list_ships(
    response: Response,
    limit: int = Query(2000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _list("ship", response, limit, offset, db)


@ships_router.get("/{slug}", response_model=HubDetail)
def get_ship(slug: str, response: Response, db: Session = Depends(get_db)):
    return _detail("ship", slug, response, db)
