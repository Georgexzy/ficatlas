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

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Ranked within a site, never across them.
#
# kudos is the popularity column and its coverage is wildly uneven: 239,588 AO3
# rows have it against 1,470 of FanFiction.net's 6.57M, and FicAlley records
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

            exact = db.execute(text(f"""
                SELECT count(*) FROM stories
                 WHERE {array_col} && :variants AND delisted_at IS NULL
            """), {"variants": variants}).scalar_one()
        except Exception:
            # One bad group must not abandon the rest of a long rebuild.
            log.exception("hub build failed for %s", slug)
            db.rollback()
            continue

        if not top:
            continue

        db.execute(text(f"""
            INSERT INTO {table} (slug, name, variants, work_count, top_ids,
                                 top_by_site, built_at, content_at)
            VALUES (:slug, :name, :variants, :wc, :top,
                    CAST(:by_site AS jsonb), now(), now())
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, variants = EXCLUDED.variants,
                work_count = EXCLUDED.work_count, top_ids = EXCLUDED.top_ids,
                top_by_site = EXCLUDED.top_by_site,
                built_at = EXCLUDED.built_at,
                -- content_at moves only when the page would actually LOOK
                -- different. It is the sitemap's <lastmod>, and Google is
                -- explicit that an inaccurate one gets ignored — bumping it on
                -- every nightly rebuild would claim all 7,584 hubs changed
                -- daily, which is both untrue and self-defeating.
                --
                -- `IS DISTINCT FROM` rather than `<>` so a NULL on either side
                -- compares as a change rather than as unknown.
                content_at = CASE
                    WHEN {table}.top_ids     IS DISTINCT FROM EXCLUDED.top_ids
                      OR {table}.work_count  IS DISTINCT FROM EXCLUDED.work_count
                      OR {table}.name        IS DISTINCT FROM EXCLUDED.name
                    THEN now()
                    ELSE {table}.content_at
                END
        """), {"slug": slug, "name": group["name"], "variants": variants,
               "wc": exact, "top": top, "by_site": json.dumps(by_site)})
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
