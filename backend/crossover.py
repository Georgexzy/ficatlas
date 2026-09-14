"""Is this work a crossover? One definition, used everywhere it is decided.

The old answer was `len(fandoms) > 1`, written out separately in five places.
It is wrong often enough to matter, because AO3 fandom tags are not franchises:
an author files one story under every spelling that fits, so a single-franchise
work routinely carries two, three or twenty-one of them.

    {"Star Wars - All Media Types", "Star Wars: The Clone Wars (2008)",
     "Star Wars: Rebels"}
    {"Percy Jackson and the Olympians - Rick Riordan",
     "Percy Jackson and the Olympians & Related Fandoms - All Media Types"}
    {"Dragon Age - All Media Types", "Dragon Age: Inquisition"}

None of those is a crossover. Measured over a 20,000-work sample of AO3 works
the old rule flagged, **20.6% carry only one franchise**.

What a franchise key throws away
--------------------------------
AO3's name furniture, and nothing else:

  * the disambiguator — `(TV)`, `(Comics)`, `(Movies)`, `(Anime & Manga)`
  * the ` - ` tail — `- All Media Types`, `- J. K. Rowling`, `- Rick Riordan`
  * AO3's `& Related Fandoms` umbrella suffix. Stripped by name rather than by
    cutting at any `&`, because an ampersand is ordinary inside a real title
  * the subtitle after `:` — `Star Wars: The Clone Wars` -> `star wars`
  * a leading article, so `The Walking Dead` and `Walking Dead` agree
  * the original-language half of `原神 | Genshin Impact`, keeping the last
    alternative, which is the English name AO3 writes second

Deliberately NOT a synonym table. The furniture is a naming convention the
archive follows, so stripping it generalises to fandoms nobody has heard of;
a list of equivalences would cover whatever somebody thought of on the day.

The one case it gets wrong, and it is worth knowing
---------------------------------------------------
`Avatar: The Last Airbender` and `Avatar (Cameron Movies)` both reduce to
`avatar`, so a genuine crossover between them reads as one franchise. That
direction is the safe one — a crossover kept is a work the reader can see and
judge, while a work wrongly hidden is invisible — and it is rarer than the
false positives the subtitle rule removes.
"""
from __future__ import annotations

import logging
import os
import re
import time

_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.I)
# AO3's umbrella suffix, e.g. "Percy Jackson and the Olympians & Related
# Fandoms - All Media Types". Matched by name, not by cutting at any "&":
# "Tom & Jerry" and "Dungeons & Dragons" are whole titles.
_UMBRELLA = re.compile(r"\s*&\s*related\s+(?:fandoms|media|works)\s*$", re.I)


def franchise(name: str) -> str:
    """The franchise an AO3 fandom tag belongs to."""
    # AO3 writes "原神 | Genshin Impact (Video Game)" — alternatives of ONE
    # name, English last. Take the last, which is what readers type.
    if "|" in name:
        name = name.rsplit("|", 1)[1]
    name = name.split(" - ", 1)[0]
    name = name.split(" (", 1)[0]
    name = name.split(":", 1)[0]
    name = _UMBRELLA.sub("", name)
    return _ARTICLE.sub("", name.strip()).strip().lower()


def is_crossover(fandoms) -> bool:
    """More than one FRANCHISE, not more than one fandom tag."""
    keys = {franchise(f) for f in (fandoms or []) if f and f.strip()}
    keys.discard("")
    return len(keys) > 1


# ── The same definition, in SQL ───────────────────────────────────────────────
#
# The repair below rewrites millions of rows, and pulling each one into Python
# to decide a boolean would take days on this box. So the rule exists twice, and
# the honest way to keep two implementations in step is the one the two query
# parsers already use: a test asserts they AGREE, over real fandom names taken
# from the index rather than over invented ones.
#
# Keep `_SQL_FRANCHISE` and `franchise()` edited together, and run
# tests/test_crossover.py, which compares them across the 500 largest fandoms.
_SQL_FRANCHISE = r"""
CREATE OR REPLACE FUNCTION fic_franchise(f text) RETURNS text AS $fn$
  SELECT lower(btrim(
    regexp_replace(
      regexp_replace(
        split_part(split_part(split_part(
          CASE WHEN f LIKE '%|%'
               -- Greedy, so it keeps the text after the LAST pipe: AO3 writes
               -- the English name second and that is what readers type.
               THEN btrim(regexp_replace(f, '^.*\|', ''))
               ELSE f END,
          ' - ', 1), ' (', 1), ':', 1),
        '\s*&\s*related\s+(fandoms|media|works)\s*$', '', 'i'),
      '^(the|a|an)\s+', '', 'i')))
$fn$ LANGUAGE sql IMMUTABLE;
"""

# ── The trigger: the same argument content_gates.py makes ───────────────────
#
# `is_crossover` is a pure function of `fandoms`, exactly as the content gates
# are of `tags` and `warnings` — so a column maintained only by a periodic job
# is stale by construction. Measured thirteen hours after a clean repair: one
# disagreement in a 5,000-row sample, from rows the crawler had written since.
#
# The importers and the crawler all call `crossover.is_crossover` now, so they
# write it correctly. The trigger is for everything that does NOT go through
# them — a bulk UPDATE to `fandoms`, a repair script, a hand-run backfill —
# which is exactly the case the gate trigger exists to cover.
_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION fic_set_is_crossover() RETURNS trigger AS $t$
BEGIN
  NEW.is_crossover := (
    SELECT count(DISTINCT fic_franchise(f)) > 1
      FROM unnest(COALESCE(NEW.fandoms, '{}')) f
     WHERE btrim(f) <> ''
  );
  RETURN NEW;
END $t$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_fic_set_is_crossover ON stories;
CREATE TRIGGER trg_fic_set_is_crossover
  BEFORE INSERT OR UPDATE OF fandoms ON stories
  FOR EACH ROW EXECUTE FUNCTION fic_set_is_crossover();
"""

# A single-fandom row can never be a crossover, and needs no function call —
# kept separate so the cheap half of the repair is an index-friendly predicate.
_REPAIR_DOWN_SQL = """
UPDATE stories SET is_crossover = false
 WHERE id IN (SELECT id FROM stories
               WHERE is_crossover
                 AND coalesce(array_length(fandoms, 1), 0) <= 1
               LIMIT :batch)
"""

_REPAIR_SQL = """
UPDATE stories st SET is_crossover = want.v
  FROM (SELECT s.id,
               (SELECT count(DISTINCT fic_franchise(f)) FROM unnest(s.fandoms) f) > 1 AS v
          FROM stories s
         WHERE coalesce(array_length(s.fandoms, 1), 0) > 1
           AND s.id > CAST(:after AS uuid)
         ORDER BY s.id
         LIMIT :batch) want
 WHERE st.id = want.id AND st.is_crossover IS DISTINCT FROM want.v
"""

_CURSOR_SQL = """
SELECT id FROM stories
 WHERE coalesce(array_length(fandoms, 1), 0) > 1
   AND id > CAST(:after AS uuid)
 ORDER BY id LIMIT :batch
"""


BATCH = int(os.getenv("CROSSOVER_BATCH", "50000"))
PAUSE_S = float(os.getenv("CROSSOVER_PAUSE_S", "5"))
# One run at a time, for the same reason as content_gates.py and
# series_wordcount.py: two passes rewriting the same rows in different orders
# is the textbook deadlock, and it has cost this box an outage once already.
_LOCK_KEY = 0x0C205501

log = logging.getLogger("crossover")


def run(dry_run: bool = False) -> dict:
    """Bring `is_crossover` in line with the definition above.

    Batched and resumable, like its two siblings: the cheap half (a row with
    one fandom that is nonetheless flagged) is an index-friendly predicate that
    is its own progress marker, and the expensive half walks the primary key
    once from a keyset cursor.
    """
    from sqlalchemy import text as _text

    from db.session import db_session, lift_statement_timeout

    with db_session() as db:
        if not dry_run and not db.execute(
                _text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            log.warning("crossover: another run holds the lock; skipping")
            return {"skipped": True}
        db.execute(_text("SET statement_timeout = 0"))
        db.execute(_text(_SQL_FRANCHISE))
        # The trigger FIRST, so anything written while the repair below runs is
        # already correct. The other way round leaves a window the length of
        # the repair — the same ordering content_gates.py uses and for the same
        # reason.
        db.execute(_text(_TRIGGER_SQL))
        db.commit()

        if dry_run:
            n = db.execute(_text("""
                SELECT count(*) FROM stories
                 WHERE coalesce(array_length(fandoms,1),0) > 1 AND is_crossover
            """)).scalar()
            log.info("crossover: %s flagged rows carry more than one fandom",
                     f"{n:,}")
            return {"candidates": n}

        cleared = 0
        while True:
            lift_statement_timeout(db)
            n = db.execute(_text(_REPAIR_DOWN_SQL), {"batch": BATCH}).rowcount
            db.commit()
            if not n:
                break
            cleared += n
            log.info("crossover: cleared %s single-fandom rows (%s so far)",
                     f"{n:,}", f"{cleared:,}")
            time.sleep(PAUSE_S)

        changed, seen = 0, 0
        after = "00000000-0000-0000-0000-000000000000"
        while True:
            lift_statement_timeout(db)
            ids = db.execute(_text(_CURSOR_SQL),
                             {"after": after, "batch": BATCH}).scalars().all()
            if not ids:
                break
            n = db.execute(_text(_REPAIR_SQL),
                           {"after": after, "batch": BATCH}).rowcount
            db.commit()
            after = str(ids[-1])
            seen += len(ids)
            changed += n
            log.info("crossover: %s seen, %s corrected (%s / %s)",
                     f"{len(ids):,}", f"{n:,}", f"{seen:,}", f"{changed:,}")
            time.sleep(PAUSE_S)

        total = db.execute(_text(
            "SELECT count(*) FROM stories WHERE is_crossover")).scalar()

    log.info("crossover: %s flagged (%s corrected, %s cleared)",
             f"{total:,}", f"{changed:,}", f"{cleared:,}")
    return {"flagged": total, "corrected": changed, "cleared": cleared}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.path.insert(0, "/app")
    from db.dsn import default_database_url
    os.environ.setdefault("DATABASE_URL", default_database_url())
    _sys.exit(main())
