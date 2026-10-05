"""Which archives a work's OTHER copies are on — so a site filter can find it.

The cross-post dedup keeps one row per story and deletes the rest, recording
their URLs in `cross_post_urls` (live_fetch/crosspost.py). The site filter
tested `site` alone, so a story kept as its AO3 row was invisible to a reader
filtering for FanFiction.net, though FF.net is exactly where it can be read:
~176,000 merged works, 124k of them FF.net rows also on AO3 and 40k the
reverse.

`copy_sites` is the set of archives named in `cross_post_urls`, NULL when there
are none. A pure function of one column on the same row, so it has a trigger,
by the argument content_gates.py and crossover.py already make: a column kept
only by a job is stale by construction. NULL rather than '{}' for the 99% with
no copies, so the partial GIN index holds only the rows that matter.

    docker exec ficatlas-worker-1 python copy_sites.py      # trigger + backfill
"""
from __future__ import annotations

import logging
import re
import sys

log = logging.getLogger("copy_sites")

# Mirrored in SQL below; tests/test_copy_sites.py holds the two together.
HOSTS = [("archiveofourown.org", "ao3"), ("fanfiction.net", "ffnet"),
         ("fictionalley.org", "fictionalley"), ("squidgeworld.org", "squidgeworld")]


def copy_sites(urls: list[str] | None) -> list[str] | None:
    out: set[str] = set()
    for u in urls or []:
        m = re.match(r"^https?://(?:www\.|m\.)?([^/:]+)", u or "")
        if not m:
            continue
        for host, site in HOSTS:
            if m.group(1) == host or m.group(1).endswith("." + host):
                out.add(site)
    return sorted(out) or None


# No non-capturing groups and no colon-words anywhere in this SQL, comments
# included: text() treats a colon followed by a word as a bind parameter, and
# a regex written with a non-capturing group around "www" failed every
# statement with "A value is required for bind parameter 'www'".
FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION fic_copy_sites(urls text[]) RETURNS text[]
LANGUAGE sql IMMUTABLE AS $f$
  SELECT NULLIF(ARRAY(
    SELECT DISTINCT s FROM (
      SELECT CASE
        WHEN h = 'archiveofourown.org' OR h LIKE '%.archiveofourown.org' THEN 'ao3'
        WHEN h = 'fanfiction.net'      OR h LIKE '%.fanfiction.net'      THEN 'ffnet'
        WHEN h = 'fictionalley.org'    OR h LIKE '%.fictionalley.org'    THEN 'fictionalley'
        WHEN h = 'squidgeworld.org'    OR h LIKE '%.squidgeworld.org'    THEN 'squidgeworld'
      END AS s
      FROM (SELECT regexp_replace(substring(u FROM '^https?://([^/:]+)'),
                                  '^(www|m)[.]', '') AS h
              FROM unnest(COALESCE(urls, '{}')) u) x) y
    WHERE s IS NOT NULL ORDER BY 1), '{}'::text[])
$f$;

CREATE OR REPLACE FUNCTION fic_set_copy_sites() RETURNS trigger AS $t$
BEGIN
  NEW.copy_sites := fic_copy_sites(NEW.cross_post_urls);
  RETURN NEW;
END $t$ LANGUAGE plpgsql;
"""

TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_fic_set_copy_sites ON stories;
CREATE TRIGGER trg_fic_set_copy_sites
  BEFORE INSERT OR UPDATE OF cross_post_urls ON stories
  FOR EACH ROW EXECUTE FUNCTION fic_set_copy_sites();
"""

BATCH = 5000


def install(db) -> None:
    from sqlalchemy import text
    db.execute(text(FUNCTION_SQL))
    # ACCESS EXCLUSIVE on stories: never let it queue in front of readers.
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    db.execute(text(TRIGGER_SQL))
    db.commit()


def backfill(db) -> int:
    """Fill rows written before the trigger. Keyset over the few with copies."""
    from sqlalchemy import text
    from db.session import lift_statement_timeout
    done, last = 0, "00000000-0000-0000-0000-000000000000"
    while True:
        lift_statement_timeout(db)
        rows = db.execute(text("""
            SELECT id FROM stories
             WHERE cross_post_urls <> '{}' AND id > CAST(:last AS uuid)
             ORDER BY id LIMIT :n
        """), {"last": last, "n": BATCH}).fetchall()
        if not rows:
            return done
        last = str(rows[-1][0])
        done += db.execute(text("""
            UPDATE stories SET copy_sites = fic_copy_sites(cross_post_urls)
             WHERE id = ANY(CAST(:ids AS uuid[]))
               AND copy_sites IS DISTINCT FROM fic_copy_sites(cross_post_urls)
        """), {"ids": [str(r[0]) for r in rows]}).rowcount or 0
        db.commit()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from db.session import db_session
    with db_session() as db:
        install(db)
        log.info("copy_sites: trigger installed; backfilled %d rows", backfill(db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
