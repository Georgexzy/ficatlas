"""Ship nicknames — "wolfstar", "drarry", "taejin" — resolved to real pairings.

Readers do not type "Kim Seokjin | Jin/Kim Taehyung | V". They type "taejin",
and the search recorded in `visit_events` says so plainly:

    Bts jin and taehyung jealousy    799 results
    Bts taejin jealousy               20 results

Same ship, same reader, forty times fewer works — because "taejin" is a
freeform TAG on 233 works, while the pairing itself is a RELATIONSHIP on far
more. The nickname is how fandom talks; the canonical string is how the archive
files it, and nothing connected the two.

`character_aliases.py` solves this by hand for ~40 Harry Potter characters,
which is the fandom that needed it most when the index was mostly FictionAlley.
It does not extend: there are thousands of these nicknames across K-pop, anime
and everything else, they are coined faster than anyone can list them, and they
are exactly the vocabulary a hand-written table cannot keep up with.

So mine them instead. A nickname earns its place by CO-OCCURRENCE: of the works
carrying the tag `wolfstar`, 93% also carry the relationship
`Sirius Black/Remus Lupin`. Nothing else comes close (the runner-up is 27%, and
is just the next most popular ship in the same corner of the fandom). That gap
is the whole signal, and it is why a threshold on share works where a threshold
on raw count would not.

Two things this deliberately does NOT do:

  * It does not rewrite the query. The alias is applied as an OR beside the
    existing full-text match, so a resolved nickname can only ADD works. A
    mis-mined alias makes results broader and slightly less relevant; it can
    never make a search return less than it does today. Given this table is
    built by inference over user-written tags, that asymmetry is the point.
  * It does not mine multi-word tags. "wolfstar fluff" resolves to the same
    ship but is already found by the full-text search, and admitting spaces
    makes every genre phrase a candidate.
"""

import logging
import os
import re
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("ship_aliases")

# A candidate must be a single word: no spaces, no separators that would make it
# a pairing already, and long enough that it is not an initialism colliding with
# ordinary words ("bts", "au", "omc").
CANDIDATE_SQL = text("""
    SELECT value, count
      FROM facets
     WHERE kind IN ('tag', 'relationship')
       AND count BETWEEN :min_works AND :max_works
       AND value !~ '[/&|]'
       -- One word, optionally with the type suffix AO3 exports. Dropping
       -- suffixed values outright is what hid `TaeJin - Freeform` (233 works)
       -- and `wolfstar - Relationship` (576) -- the exact nicknames this table
       -- exists for, and in taejin's case the one the traffic log caught
       -- failing.
       AND value ~ '^\\S+( - (Freeform|Relationship|Character|Fandom))?$'
       AND length(value) BETWEEN 5 AND 40
     ORDER BY count DESC
""")

# The modal relationship among works carrying the candidate. LIMIT bounds the
# work per candidate: a sample of a few thousand settles a 90%-vs-27% question
# many times over, and there are thousands of candidates to get through.
MODAL_SQL = text("""
    WITH s AS (
        SELECT relationships, fandoms
          FROM stories
         WHERE (tags @> ARRAY[:alias] OR relationships @> ARRAY[:alias])
           -- Works with no relationship data can neither support nor refute an
           -- association, and including them only deflates every share by
           -- however much of the sample came from the bulk metadata dumps.
           -- They also made the sample itself unstable: LIMIT without a
           -- predicate returns whichever rows the plan reaches first, so a
           -- planner change silently altered every number.
           AND array_length(relationships, 1) IS NOT NULL
         LIMIT :sample
    ),
    total AS (SELECT count(*) n FROM s),
    rel AS (
        SELECT r, count(*) hits FROM s, unnest(s.relationships) r
         WHERE r !~ '&' GROUP BY r ORDER BY count(*) DESC LIMIT 1
    ),
    fan AS (
        SELECT count(*) hits FROM s, unnest(s.fandoms) f
         GROUP BY f ORDER BY count(*) DESC LIMIT 1
    )
    SELECT rel.r, rel.hits, (SELECT n FROM total),
           COALESCE((SELECT hits FROM fan), 0)
      FROM rel
""")

# Below this share the tag is a genre word, not a nickname: "Fluff" co-occurs
# with a great many pairings and dominates none of them.
MIN_SHARE = 0.55

# A real nickname belongs to ONE fandom. This is what separates "wolfstar" from
# "canon", "futurefic" and "hurt-comfort", which the share threshold alone let
# through: those are used everywhere, so they land on whatever pairing happens
# to be biggest among the works that carry them (`canon` resolved to a Queer as
# Folk ship on 57%, and would then have widened every search containing the
# word "canon" with those works). A genre word spans thousands of fandoms and
# concentrates in none.
MIN_FANDOM_SHARE = 0.60
MIN_HITS = 40
MIN_WORKS = 100
MAX_WORKS = 50_000     # above this it is a genre or a fandom, not a ship name
SAMPLE = 4000

# A nickname that is also an ordinary English word would fire on queries that
# have nothing to do with the ship. These are the ones that actually appear in
# the vocabulary; the share threshold does not exclude them because within
# fanfiction they really are used as the ship name.
STOP_ALIASES = {
    "harmony", "romance", "friendship", "family", "endgame",
    "soulmates", "polyamory", "threesome", "marriage", "wedding",

    # Language tags. A translation is tagged with its language, and a fandom
    # translated mostly by one circle concentrates on that circle's ship —
    # "ukrainian" mined to Dramione at 60% on exactly that path. Someone
    # searching for a language means the language.
    "ukrainian", "russian", "spanish", "french", "german", "italian", "polish",
    "portuguese", "chinese", "korean", "japanese", "indonesian", "vietnamese",
    "turkish", "arabic", "filipino", "tagalog", "espanol", "deutsch",

    # An era, a place or an event in canon, not a pairing. These are
    # fandom-specific, so the fandom-concentration rule cannot see them.
    "marauders", "reichenbach", "enochian", "hogwarts", "avengers",
    "quidditch", "asgard", "gotham", "konoha",
}


_AO3_SUFFIX = re.compile(r"\s*-\s*(Freeform|Relationship|Character|Fandom)$", re.I)


def _strip_suffix(value: str) -> str:
    """`TaeJin - Freeform` is the tag `taejin` wearing AO3's type label."""
    return _AO3_SUFFIX.sub("", value).strip()


def _looks_like_a_word(value: str) -> bool:
    """A run-together portmanteau, which is what a ship nickname is.

    No hyphen: era and genre tags carry one — "post-reichenbach",
    "hurt-comfort", "john-centric" — and all three mined to a real pairing on a
    50-60% share because they are fandom-specific enough to pass every other
    test. Nothing is lost by the rule; ship names do not hyphenate.
    """
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9']{4,23}", value))


def mine(limit: int | None = None, verbose: bool = False) -> list[dict]:
    """Return alias -> canonical relationship, with the evidence for each."""
    found = []
    with db_session() as db:
        db.execute(text("SET statement_timeout = 0"))
        candidates = db.execute(CANDIDATE_SQL, {
            "min_works": MIN_WORKS, "max_works": MAX_WORKS}).fetchall()
        log.info("%d candidate nicknames", len(candidates))

        seen = 0
        for value, works in candidates:
            if limit is not None and seen >= limit:
                break
            # Probe with the value as the archives wrote it — array containment
              # is exact — but store and match on the bare nickname.
            probe = value.strip()
            alias = _strip_suffix(probe)
            if not _looks_like_a_word(alias) or alias.lower() in STOP_ALIASES:
                continue
            seen += 1

            row = db.execute(MODAL_SQL, {"alias": probe, "sample": SAMPLE}).fetchone()
            if not row:
                continue
            relationship, hits, sampled, fandom_hits = row
            if not sampled or hits < MIN_HITS:
                continue
            share = hits / sampled
            if share < MIN_SHARE:
                continue
            if fandom_hits / sampled < MIN_FANDOM_SHARE:
                continue
            # A nickname resolving to itself is the vocabulary already agreeing.
            if relationship.strip().lower() == alias.lower():
                continue

            found.append({"alias": alias.lower(), "relationship": relationship,
                          "works": works, "share": round(share, 3)})
            if verbose:
                log.info("  %-22s -> %-50s %.0f%% of %d (fandom %.0f%%)",
                         alias, relationship[:50], share * 100, sampled,
                         fandom_hits / sampled * 100)
    return found


def rebuild(limit: int | None = None, verbose: bool = False) -> int:
    rows = mine(limit=limit, verbose=verbose)
    if not rows:
        log.warning("mined nothing — leaving the existing table alone")
        return 0
    with db_session() as db:
        db.execute(text("SET statement_timeout = 0"))
        db.execute(text("CREATE TABLE IF NOT EXISTS ship_aliases ("
                        "alias text PRIMARY KEY, relationship text NOT NULL, "
                        "works integer, share real, built_at timestamp DEFAULT now())"))
        db.execute(text("DELETE FROM ship_aliases"))
        for r in rows:
            db.execute(text("INSERT INTO ship_aliases (alias, relationship, works, share) "
                            "VALUES (:alias, :relationship, :works, :share) "
                            "ON CONFLICT (alias) DO NOTHING"), r)
        db.commit()
        # Not len(rows): a nickname can be mined twice, once bare and once with
        # AO3's type suffix, and ON CONFLICT keeps the more-used one.
        stored = db.execute(text("SELECT count(*) FROM ship_aliases")).scalar()
    log.info("ship_aliases: %d nicknames (%d mined)", stored, len(rows))
    return stored


def lookup(db, term: str) -> str | None:
    """The canonical relationship a nickname stands for, or None."""
    t = term.strip().lower()
    if not t or " " in t:
        return None
    try:
        row = db.execute(text("SELECT relationship FROM ship_aliases WHERE alias = :a"),
                         {"a": t}).fetchone()
        return row[0] if row else None
    except Exception:
        return None      # table not built yet — caller falls back to full text


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="only consider the N most-used candidates (trial runs)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        for r in mine(limit=a.limit, verbose=True):
            print(f"{r['alias']:<22} {r['relationship'][:60]:<60} "
                  f"{r['share']*100:.0f}%  {r['works']}")
    else:
        rebuild(limit=a.limit, verbose=True)
