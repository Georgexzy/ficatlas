"""The half of hub building that does not care what a hub is *of*.

fandom_hubs.py and ship_hubs.py differ only in how they collapse facet rows into
groups — an author suffix for fandoms, pairing order for ships. Everything after
that is identical: rank the group's works within each archive, write one row,
prune what this run did not touch. That part lives here so there is one copy of
it to be right, and so a fix to the pruning rule (see below) applies to both.

The caller supplies an already-collapsed mapping and the table to write to; this
module owns the ranking SQL, the batching, and the sweep.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Ranked within a site, never across them.
#
# kudos is the popularity column and its coverage is wildly uneven: 239,588 AO3
# rows have it against 1,470 of FanFiction.net's 6.57M, and FictionAlley records
# hits on 29,864 of its 29,949 rows and kudos on 27. A single ORDER BY kudos
# across all three therefore returned AO3 and only AO3.
#
# Sorting them against each other was never meaningful anyway: an AO3 kudos and
# a FanFiction.net favourite are different units counted by different
# populations. So each archive is ranked on its own and shown in its own
# section, and the fallbacks run kudos -> hits -> word_count so that a site with
# no popularity data at all still produces a sensible list rather than an empty
# one.
RANK = ("kudos DESC NULLS LAST, hits DESC NULLS LAST, "
        "word_count DESC NULLS LAST")

# How many works to sample when asking what a hub is ABOUT, and how many
# qualities to keep. The sample is unordered on purpose: "what do works in this
# corner of the archive tend to be tagged" is a question about the whole hub,
# and ranking the sample by popularity would answer a different one.
TAG_SAMPLE = 4000
TAGS_PER_HUB = 8

# Tags that describe the ARTEFACT rather than the story. Every one is a real,
# well-used AO3 tag and none of them is a reason to pick one fic over another,
# which is the only job these chips have. Written out rather than derived:
# there is no rule over a tag's text that separates "this is a podfic" from
# "this is slow burn", and the set is small and stable.
NOT_A_QUALITY = {
    # The artefact rather than the story.
    "cover art", "fanart", "art", "podfic", "podfic welcome",
    "translation", "translation available", "fanvid", "video", "illustrated",
    "not a fic", "meta", "essay", "playlist", "moodboard", "edit",
    # The WRITING PROCESS rather than the story. These are the author talking
    # to their readers — where it was posted, whether anyone checked it, how
    # long it took — and a reader choosing between two fics is not choosing on
    # any of them.
    "not beta read", "beta read", "unbeta'd", "unbetaed", "no beta",
    "originally posted on tumblr", "originally posted on livejournal",
    "crossposted on fanfiction.net", "crossposted", "reposted",
    "inspired by fanfiction", "inspired by", "work in progress",
    "first fanfic", "first work", "author is bad at tagging",
    "bad at summaries", "tags will be added", "tags may change",
    "drabble", "flash fic", "ficlet", "short", "one shot", "oneshot",
}


def quality_vocabulary(db: Session) -> tuple[dict[str, int], set[str]]:
    """The tags worth offering as a refinement, loaded ONCE for a whole run.

    Doing this per hub was the first version and it cost 73 to 94 SECONDS each
    — a NOT EXISTS against `facets` and a lookup in `tag_prose` for every
    distinct tag in the sample — against 0.06s for the sample itself. Over
    7,584 hubs that is a nightly job that never finishes. The filters are the
    same for every hub, so they belong outside the loop.

    Two sources, and both already exist:

      * the largest TAG facets, which is what makes a chip worth clicking —
        a refinement nobody else uses is a dead end dressed as a door, and
        taking the top slice also drops the importer's own bookkeeping
        (`ao3_meta_dump`, `ffnet_dump`, `janelleshane_seed`) without naming it.
      * `tag_prose`, which already measures whether a word is a term of art or
        just English. `Fluff` is tagged four times more often than it is
        written and `Sad` a third as often; the same ratio that keeps `Sad` out
        of an extracted query keeps it off a hub page.
    """
    tags = {r[0]: r[1] for r in db.execute(text("""
        SELECT value, max(count) FROM facets
         WHERE kind = 'tag' AND count >= :min
         GROUP BY 1 ORDER BY 2 DESC LIMIT :lim
    """), {"min": 500, "lim": 4000}).fetchall()}
    prose: set[str] = set()
    try:
        prose = {r[0] for r in db.execute(text(
            "SELECT tag FROM tag_prose WHERE ratio < 0.5")).fetchall()}
    except Exception:
        # Built offline and absent on a fresh install. Without it the chips are
        # merely less well filtered, which is not a reason to fail a rebuild.
        log.debug("tag_prose unavailable; hub tags unfiltered by prose ratio")
    return tags, prose


def hub_qualities(rows: list, name: str, variants: list[str],
                  vocab: dict[str, int], prose: set[str],
                  nicknames: list[str] | None = None) -> list[dict]:
    """Which qualities this hub's works actually have, filtered in memory.

    Excludes anything that restates the hub. A Drarry page offering
    "Draco Malfoy" as a refinement is offering the reader what they already
    clicked, and the check is on WORDS rather than on the whole string so that
    `Bottom Draco Malfoy` goes too — a pairing hub is not the place to split by
    position, and it crowds out the qualities that do distinguish one fic from
    another.
    """
    # Including the hub's NICKNAMES, which is what keeps `wolfstar` off the
    # Remus/Sirius page. A portmanteau shares no words with the names it is
    # made of, so the word check below cannot see it.
    own = {w for v in list(variants) + [name] + list(nicknames or [])
           for w in re.findall(r"[a-z0-9']+", v.lower()) if len(w) > 2}
    own |= {n.lower() for n in (nicknames or [])}
    counts: dict[str, int] = {}
    for (tag,) in rows:
        counts[tag] = counts.get(tag, 0) + 1
    out = []
    for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if tag in prose or tag not in vocab:
            continue
        low = tag.lower()
        if low in NOT_A_QUALITY or low in own:
            continue
        # AO3 marks a freeform duplicate of a canonical tag with this suffix,
        # and it is always the worse spelling of something already here —
        # `Destiel - Freeform` beside `Castiel/Dean Winchester`. It is also how
        # a CROSSOVER leaks somebody else's ship onto this page: the
        # Castiel/Dean hub was offering `Johnlock - Freeform`.
        if low.endswith(" - freeform"):
            continue
        words = set(re.findall(r"[a-z0-9']+", low))
        if words & own:
            continue
        out.append({"tag": tag, "works": n})
        if len(out) >= TAGS_PER_HUB:
            break
    return out


def build_groups(
    db: Session,
    *,
    table: str,
    array_col: str,
    groups: dict[str, dict],
    per_hub: int,
    prune: bool = True,
) -> int:
    """Rank and write one row per group. Returns the number written.

    `groups` maps slug -> {"name", "variants", "approx"}; `array_col` is the
    stories column its variants are matched against with && (fandoms or
    relationships), both of which carry a GIN index.

    Offline by design — this is minutes of work, not a request path.

    `prune` deletes hubs this run did not write, which is how a fandom that fell
    below the threshold or lost all its works stops being served as a stale page
    that keeps getting crawled. It MUST be false for a partial run: a `--limit 10`
    trial would otherwise delete every hub it did not rebuild.
    """
    # `table` and `array_col` are interpolated into SQL, so they are checked
    # against a literal allowlist rather than trusted. They come from module
    # constants today; this keeps that true if a caller ever passes user input.
    if table not in ("fandom_hubs", "ship_hubs"):
        raise ValueError(f"refusing to build unknown table {table!r}")
    if array_col not in ("fandoms", "relationships"):
        raise ValueError(f"refusing to match on unknown column {array_col!r}")

    db.execute(text("SET statement_timeout = 0"))

    # Loaded once for the whole run. See `quality_vocabulary` — per hub this
    # was 73-94 seconds against 0.06s for the sample it was filtering.
    vocab, prose = quality_vocabulary(db)

    # The database's clock, not Python's, because it is the same clock that
    # stamps built_at — comparing against a locally-taken time would drift.
    started = db.execute(text("SELECT now()")).scalar_one()

    ordered = sorted(groups.items(), key=lambda kv: -kv[1]["approx"])

    written = 0
    for slug, group in ordered:
        variants = group["variants"]
        try:
            # One pass, partitioned by site: a window function ranks within each
            # archive so a single scan produces all three lists. Three separate
            # queries would triple the cost of a rebuild that already takes
            # minutes over millions of matching rows.
            ranked = db.execute(text(f"""
                SELECT site, id FROM (
                    SELECT site, id,
                           row_number() OVER (PARTITION BY site
                                              ORDER BY {RANK}) AS rn
                      FROM stories
                     WHERE {array_col} && :variants
                       AND delisted_at IS NULL
                       AND source_restricted_at IS NULL
                ) ranked
                 WHERE rn <= :n
                 ORDER BY site, rn
            """), {"variants": variants, "n": per_hub}).fetchall()

            by_site: dict[str, list[str]] = {}
            for site, sid in ranked:
                by_site.setdefault(str(site), []).append(str(sid))

            # top_ids stays populated as a flat interleave of the per-site lists,
            # so anything reading the old column still gets a sensible, and now
            # cross-archive, ordering.
            top = []
            for i in range(per_hub):
                for site in sorted(by_site):
                    if i < len(by_site[site]):
                        top.append(by_site[site][i])

            # The total AND its split by archive, from the one count that was
            # already being paid for.
            #
            # The split is what decides which archive leads the page, and
            # getting it from the listing instead was wrong in a way that
            # showed: the sections were ordered by how many works each archive
            # contributed to the list, which is capped at `per_hub`, so any two
            # archives that both filled their quota tied and fell back to
            # whatever order the dictionary happened to be in. On
            # `Draco Malfoy/Harry Potter` — 53,210 works and one of the largest
            # pairings on AO3 — that put FICTIONALLEY first, an archive that
            # holds a few hundred of them. The page that earns the most search
            # traffic on this site opened with the least of what the reader
            # came for.
            # WHAT WORKS HERE ARE ACTUALLY LIKE, for the refinement chips.
            # An unordered sample, because the question is about the hub and
            # not about its best-read corner.
            tag_rows = db.execute(text(f"""
                SELECT unnest(tags) FROM stories
                 WHERE {array_col} && :variants
                   AND delisted_at IS NULL
                   AND NOT gate_underage AND NOT gate_adult
                 LIMIT {TAG_SAMPLE}
            """), {"variants": variants}).fetchall()
            qualities = hub_qualities(tag_rows, group["name"], variants,
                                      vocab, prose, group.get("nicknames"))

            per_site = db.execute(text(f"""
                SELECT site, count(*) FROM stories
                 WHERE {array_col} && :variants AND delisted_at IS NULL
                 GROUP BY site
            """), {"variants": variants}).fetchall()
            site_counts = {str(k): int(v) for k, v in per_site}
            exact = sum(site_counts.values())
        except Exception:
            # One bad group must not abandon the rest of a long rebuild.
            log.exception("hub build failed for %s", slug)
            db.rollback()
            continue

        if not top:
            continue

        db.execute(text(f"""
            INSERT INTO {table} (slug, name, variants, work_count, top_ids,
                                 top_by_site, site_counts, qualities,
                                 built_at, content_at)
            VALUES (:slug, :name, :variants, :wc, :top,
                    CAST(:by_site AS jsonb), CAST(:counts AS jsonb),
                    CAST(:quals AS jsonb), now(), now())
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, variants = EXCLUDED.variants,
                work_count = EXCLUDED.work_count, top_ids = EXCLUDED.top_ids,
                top_by_site = EXCLUDED.top_by_site,
                site_counts = EXCLUDED.site_counts,
                qualities = EXCLUDED.qualities,
                built_at = EXCLUDED.built_at,
                -- content_at moves only when the page would actually LOOK
                -- different. It is the sitemap's <lastmod>, and Google is
                -- explicit that an inaccurate one gets ignored — bumping it on
                -- every nightly rebuild would claim all 7,584 hubs changed
                -- daily, which is both untrue and self-defeating.
                --
                -- That was the intent and it was not achieved. Measured
                -- 2026-09-08: 3,185 of 6,165 ship hubs had content_at set that
                -- day, and 963 the day before — 52% of the sitemap claiming to
                -- have changed today, every day, which is precisely the pattern
                -- the comment above set out to avoid. Two of the three tests
                -- were too sensitive:
                --
                --   * work_count moves for almost every popular ship every day,
                --     because the crawler indexes ~15,000 works a day. Going
                --     from 52,120 to 52,121 is not a reason to re-fetch a page.
                --     It has to move MATERIALLY — 1%, or 10 works on a small
                --     hub, whichever is larger. The floor is what keeps small
                --     hubs honest: 1% of 90 works is one work, and a hub that
                --     gains ten has visibly changed.
                --   * top_ids is the whole list, so a swap at position 140
                --     counted as a change. Only the leading entries are what a
                --     reader or a crawler sees the page as being about, so only
                --     those are compared.
                --
                -- The name still counts on its own: that is the page's identity
                -- and the thing it ranks for.
                --
                -- `IS DISTINCT FROM` rather than `<>` so a NULL on either side
                -- compares as a change rather than as unknown.
                content_at = CASE
                    WHEN {table}.name IS DISTINCT FROM EXCLUDED.name
                      OR {table}.top_ids[1:20]
                           IS DISTINCT FROM EXCLUDED.top_ids[1:20]
                      OR abs({table}.work_count - EXCLUDED.work_count)
                           > greatest(10, {table}.work_count / 100)
                    THEN now()
                    ELSE {table}.content_at
                END
        """), {"slug": slug, "name": group["name"], "variants": variants,
               "wc": exact, "top": top, "by_site": json.dumps(by_site),
               "counts": json.dumps(site_counts),
               "quals": json.dumps(qualities)})
        written += 1
        if written % 200 == 0:
            db.commit()
            log.info("built %d %s", written, table)

    db.commit()

    if prune:
        # Anything not touched by THIS run, measured against the run's own start
        # rather than a fixed interval.
        #
        # This was `built_at < now() - interval '1 hour'`, which quietly assumes
        # the rebuild finishes inside an hour. The fandom build takes ~4 minutes
        # today, so the margin is large — but the assumption is invisible, and
        # the failure mode if it is ever wrong is severe and silent: groups are
        # written largest-first, so the rows aged past the cutoff would be the
        # biggest fandoms, deleted by their own rebuild. Comparing against the
        # start of the run cannot go wrong however long the run takes.
        stale = db.execute(text(f"DELETE FROM {table} WHERE built_at < :t"),
                           {"t": started}).rowcount
        db.commit()
        if stale:
            log.info("removed %d stale rows from %s", stale, table)

    return written
