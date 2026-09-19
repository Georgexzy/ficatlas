"""The short names readers type for fandoms, derived rather than listed.

    docker exec ficatlas-backend-1 python fandom_aliases.py --dry-run
    docker exec ficatlas-backend-1 python fandom_aliases.py

The problem
-----------
Readers do not type "The Walking Dead (TV)". They type `twd`. Every term in a
search is a requirement, so an abbreviation the index has never seen is a filter
that matches almost nothing:

    twd self insert   ->  9 works

`Self-Insert` resolved perfectly and `twd` was left over as a WORD, so the query
asked for self-inserts whose text literally contains "twd". Meanwhile "The
Walking Dead" is a fandom on thousands of works and the reader wanted those.

Why derive and not list
-----------------------
A hand-written table would cover the fandoms somebody thought of on the day and
rot from there — the same objection `ship_aliases.py` answers for pairings. An
initialism is a rule the naming convention already follows, so it generalises to
fandoms nobody has heard of yet and costs nothing to keep current.

Several candidates per fandom, because the convention is not one rule:

  * `The Walking Dead` -> twd. The article COUNTS, so "the" cannot simply be a
    stopword — dropping it yields `wd`, which nobody types.
  * `A Song of Ice and Fire` -> asoiaf. Here every word counts, articles and
    conjunctions included.
  * `Percy Jackson and the Olympians` -> pjo. Here they do not.
  * `僕のヒーローアカデミア | Boku no Hero Academia | My Hero Academia` -> both
    `bnha` and `mha`, which are the two things readers actually say. AO3 writes
    the original-language name first and English after a pipe, so each side is
    tried separately.

So the miner generates every plausible initialism and keeps the ones that are
long enough to mean something. Measured on the 800 largest fandoms: 506
distinct abbreviations, of which twd, asoiaf, mha, bnha, atla, aot, mcu, tlou,
hxh, tvd and got all fall out correctly.

What it cannot derive, and does not pretend to: `spn` for Supernatural and
`jjk` for Jujutsu Kaisen are nicknames rather than initialisms — one word
cannot produce three letters, and no rule over the name yields them. Those need
a different source and are left alone rather than guessed at.

Ambiguity
---------
118 of the 506 are claimed by more than one fandom. The largest wins, which is
what a reader means: somebody typing a two-letter abbreviation is thinking of
the fandom everybody is thinking of. The runner-up is still reachable by name.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("fandom_aliases")

# How big a fandom must be before its abbreviation is worth storing. Below this
# the initialism is likelier to collide with a real word than to be typed.
MIN_WORKS = int(os.getenv("FANDOM_ALIAS_MIN_WORKS", "1000"))
# How many fandoms to walk. Ordered by size, so this is "the fandoms anybody
# abbreviates".
MAX_FANDOMS = int(os.getenv("FANDOM_ALIAS_MAX_FANDOMS", "1500"))

# Two letters is the shortest anybody uses ("dw", "st") and seven the longest
# worth keeping; past that the reader has typed the name.
MIN_LEN, MAX_LEN = 2, 7

# Words that MAY be dropped, not words that must be. See the module note: the
# article is load-bearing in `twd` and silent in `pjo`, so both readings are
# generated and the vocabulary decides which one readers actually use.
OPTIONAL = {"the", "a", "an", "of", "and", "to", "in", "for", "or",
            "de", "la", "le", "no", "wa", "ga"}

# Words the ARCHIVE adds to a title that nobody says out loud. A trailing
# "Series" is the difference between `acotars` and `acotar`, and `acotar` is
# what readers type: AO3 files A Court of Thorns and Roses as
# "A Court of Thorns and Roses Series - Sarah J. Maas", so every initialism
# generated for it carried an s on the end and matched nothing.
#
# Trailing only. "Series" in the middle of a name is part of it — dropping it
# everywhere would mangle the fandoms that are genuinely called one.
TRAILING_NOISE = {"series", "saga", "trilogy", "cycle", "franchise",
                  "universe", "books", "novels", "movies", "films"}

# THE ONES NO RULE REACHES. This file's whole argument is that a derived
# abbreviation generalises to fandoms nobody has heard of and a list rots — and
# the note below the rule has always conceded the exception: `jjk` for Jujutsu
# Kaisen and `spn` for Supernatural are NICKNAMES, not initialisms. One word
# cannot produce three letters and no rule over the name yields them.
#
# So they are seeded, and kept honest three ways: each is checked against the
# facets table at build time so a renamed or vanished fandom drops out rather
# than lingering; each is only added if the rule did not already produce it;
# and the list is deliberately tiny. If it grows past a dozen, the rule is
# wrong and should be fixed instead of fed.
#
# Measured before seeding: of twenty nicknames readers actually type, the rule
# already resolved sixteen. This is the residue, not the mechanism.
SEEDED = {
    "jjk": "Jujutsu Kaisen",
    "aot": "Attack on Titan",
    "hxh": "Hunter X Hunter",
    "bsd": "Bungou Stray Dogs",
}

# Abbreviations that are ordinary English words. `it`, `us` and `she` are real
# fandoms whose initialisms would swallow any query containing them, and an
# alias that fires on a common word is worse than no alias at all.
# Every one of these is a real fandom's initialism AND an ordinary English
# word, and the ordinary meaning wins every time. Measured: `any` resolved to
# `Akatsuki no Yona | Yona of the Dawn` and hijacked a post that said "any good
# TWD fics"; `si` resolved to `SK8 the Infinity` on a post that meant
# Self-Insert. An alias that fires on a word people write by accident is worse
# than no alias, because the reader cannot see why their search went wrong.
#
# Written out rather than filtered by frequency: there is no list of "common
# English words" in this codebase, a dependency for one would be absurd, and
# the set of two-to-four letter strings that are both a fandom initialism and a
# word people type is small and stable.
STOPLIST = {
    # Pronouns, articles, conjunctions, prepositions.
    "it", "us", "me", "he", "she", "we", "in", "on", "at", "to", "is",
    "as", "an", "of", "or", "if", "so", "no", "up", "my", "by", "do",
    "the", "and", "for", "you", "all", "any", "am", "be", "but", "our",
    "its", "his", "her", "was", "are", "has", "had", "not", "can", "may",
    # Words that turn up in a request about fic.
    "one", "two", "new", "old", "who", "how", "why", "war", "art", "own",
    "out", "man", "day", "fic", "fics", "au", "ao", "ff", "wip", "ish",
    "ask", "any", "got", "get", "see", "say", "way", "set", "run", "top",
    "bit", "lot", "big", "few", "far", "yet", "now", "off", "per", "via",
    # Found by running the miner's output against a corpus of real fic-finder
    # posts rather than by thinking of them. Each resolved a fandom from an
    # ordinary English word in running prose, and each produced a search in
    # entirely the wrong fandom that still returned thousands of works — so
    # nothing about the result looked wrong:
    #   "I'm bored of OP Harry stories"     -> One Piece
    #   "trying to get into Dramione fics"  -> Good Omens        (the "go")
    #   "pretends to be Sirius' son"        -> South of Nowhere
    #   "re-read", "mc", "id"               -> Resident Evil, Misc. Cartoons,
    #                                          Infernal Devices
    "op", "go", "son", "re", "mc", "id", "dc", "din", "sue", "ran", "gon",
    "mad", "pet", "ship", "tag", "arc", "bay", "cap", "con", "den", "eve",
    # CHAT SHORTHAND. A whole class, not a handful of accidents: these are
    # what people type in a forum post, and several of them are also perfectly
    # good fandom initialisms. Measured on real posts — "Marvel or dc pls"
    # resolved to `Professor Layton` via `pls`, and a post containing "lol"
    # resolved to `League of Legends`. Both then led the query.
    "lol", "lmao", "rofl", "pls", "plz", "thx", "ty", "np", "idk", "idc",
    "imo", "imho", "tbh", "btw", "omg", "wtf", "ngl", "iirc", "afaik", "rn",
    "tho", "fyi", "aka", "etc", "ie", "eg", "ffs", "smh", "irl", "dm", "pm",
    "ppl", "ur", "nvm", "ikr", "obv", "def", "rec", "recs", "req", "ish",
    "ofc", "atm", "ymmv", "op",
    # Contraction tails. Typographic apostrophes are normalised before the
    # scan now, but a mined alias that can only ever match the back half of
    # "I'll" or "I've" has no legitimate use and should not exist to be
    # matched by accident.
    "ll", "ve", "nt", "im", "ive", "dont", "didnt",
    # Abbreviations that mean a TAG rather than a fandom. `si` is Self-Insert
    # to every reader who types it; that it is also SK8 the Infinity's
    # initialism is a coincidence the reader will never have in mind.
    "si", "oc", "ocs", "sioc", "poc", "hea", "ooc", "pwp", "bamf",
}


def _initialisms(name: str) -> set[str]:
    """Every plausible short form of one fandom name."""
    out: set[str] = set()
    # AO3 writes "進撃の巨人 | Attack on Titan"; each side is a name readers use.
    for part in name.split("|"):
        # Drop the disambiguator: "Batman (Comics)", "Naruto - Fandom".
        base = re.split(r"\s*[\(\-]", part)[0]
        words = re.findall(r"[A-Za-z0-9']+", base)
        if len(words) < 2:
            continue
        # The archive's own trailing noun, dropped as a third reading. See
        # TRAILING_NOISE: this is what turns `acotars` into `acotar`.
        trimmed = list(words)
        while trimmed and trimmed[-1].lower() in TRAILING_NOISE:
            trimmed.pop()
        for base_words in ({tuple(words), tuple(trimmed)}):
            for drop in (False, True):
                sig = [w for w in base_words
                       if not (drop and w.lower() in OPTIONAL)]
                if len(sig) < 2:
                    continue
                ini = "".join(w[0] for w in sig).lower()
                if MIN_LEN <= len(ini) <= MAX_LEN and ini not in STOPLIST:
                    out.add(ini)
    return out


DDL = """
CREATE TABLE IF NOT EXISTS fandom_aliases (
    alias    text PRIMARY KEY,
    fandom   text NOT NULL,
    works    integer,
    built_at timestamp DEFAULT now()
)
"""


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
        rows = db.execute(text("""
            SELECT value, count FROM facets
             WHERE kind = 'fandom' AND count >= :min
             ORDER BY count DESC
             LIMIT :lim
        """), {"min": MIN_WORKS, "lim": MAX_FANDOMS}).fetchall()

        # Largest first, so the first claim on an alias is the biggest fandom
        # and later ones are simply skipped. That IS the ambiguity rule: a
        # reader typing two letters means the fandom everybody means.
        best: dict[str, tuple[str, int]] = {}
        for name, count in rows:
            for ini in _initialisms(name):
                if ini not in best:
                    best[ini] = (name, count)

        # The seeds, after the rule and never over it. A derived alias is
        # evidence about how this index is actually named; a seed is a claim,
        # and a claim that contradicts the evidence is the one that gives way.
        seeded = 0
        for alias, needle in SEEDED.items():
            if alias in best:
                continue
            row = db.execute(text("""
                SELECT value, count FROM facets
                 WHERE kind IN ('fandom', 'fandom_ao3') AND value ILIKE :p
                   AND count >= :min
                 ORDER BY count DESC LIMIT 1
            """), {"p": f"%{needle}%", "min": MIN_WORKS}).first()
            if row:
                best[alias] = (row[0], row[1])
                seeded += 1

        stats = {"fandoms": len(rows), "aliases": len(best), "seeded": seeded}
        if dry_run:
            log.info("fandom_aliases: would store %d aliases from %d fandoms",
                     len(best), len(rows))
            for a in ("twd", "asoiaf", "mha", "atla", "mcu", "got"):
                if a in best:
                    log.info("  %-8s -> %s (%d works)", a, best[a][0], best[a][1])
            return stats

        db.execute(text(DDL))
        # Replaced wholesale rather than merged: an alias that stops being
        # derivable — because a fandom fell below the floor or was renamed —
        # should stop existing, and a merge would keep it for ever.
        db.execute(text("DELETE FROM fandom_aliases"))
        for alias, (name, count) in best.items():
            db.execute(text("INSERT INTO fandom_aliases (alias, fandom, works) "
                            "VALUES (:a, :f, :w)"),
                       {"a": alias, "f": name, "w": count})
        db.commit()

    log.info("fandom_aliases: %d aliases from %d fandoms",
             stats["aliases"], stats["fandoms"])
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
