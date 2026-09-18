"""Which one-word tags are really just English, measured against the archive.

    docker exec ficatlas-backend-1 python tag_prose.py --dry-run
    docker exec ficatlas-backend-1 python tag_prose.py

The problem
-----------
The extractor looks every 1-to-4 word run of a post up against `facets` and
keeps what the vocabulary recognises. That is the right idea and it has one
systematic failure: a great many ordinary English words are also real tags on
real works, so running prose resolves to them and the query goes out asking for
a quality the reader never mentioned.

Measured on real fic-finder posts, every one of these was CHOSEN over a term
the post was actually about:

    "shares his past"                -> tag:"Past"       (2,964 works)
    "the nerdy sweet dynamic"        -> tag:"Sweet"      (17,170)
    "Sad to see her die"             -> tag:"Sad"        (32,891)
    "black brothers and/or jegulus"  -> tag:"Brothers"   (11,729)
    "I wouldn't mind longer ones"    -> tag:"mind"       (56)

The last one is the shape of the whole problem: the reader is being served a
filter built out of their own politeness.

Why a ratio, and why not a word list
------------------------------------
The first thing tried was prose frequency alone — how often the word turns up
in a work summary — and it does not separate: `fluff` appears in 1.28% of
summaries and `sad` in 0.42%, so the junk word is the RARER one. Frequency
says how common a word is, and that was never the question.

The question is whether a reader writing this word means the tag, and the
archive answers it directly: compare how often the word is TAGGED with how
often it is merely WRITTEN. A word people tag with far more than they write is
a term of art; one people write constantly and tag rarely is English. Measured
over an 88,000-summary sample:

    tag            tagged     written     ratio
    Angst         868,737     133,423      6.51
    Jegulus         2,072         236      8.77
    Whump          45,105       8,737      5.16
    Fluff       1,130,841     232,133      4.87
    Slow Burn     193,882      51,007      3.80
    ------------------------------------------- the gap
    Sad            32,891      84,068      0.39
    Marauders       5,693      26,448      0.22
    Brothers       11,729      97,529      0.12
    Dark           32,828     384,448      0.09
    Past            2,964     494,493      0.01

The threshold is 0.5, which is well below the gap, because this is a judgement
about SOMEBODY ELSE'S WORDS and the cost of the two mistakes is not symmetric.
Demoting a word the reader meant loses them a filter they asked for; keeping
one they did not mean costs a slot in a four-term query. At 1.0 the cut also
takes `Gay` (0.92), `Murder` (0.87), `Swearing` (0.86) and `Friendship` (0.85)
— all of them things a reader plausibly writes on purpose. At 0.5 it still
catches every accident measured on a real post, and spares every arguable one.

Derived and not listed, for the reason fandom_aliases.py gives: a hand-written
list covers the words somebody thought of on the day and rots from there, while
a ratio over the current vocabulary keeps pace with it.

PHRASES ARE MEASURED AS PHRASES
-------------------------------
The first version measured one-word tags only, on the theory that a phrase is
never a prose accident. It is — `tag:"you know"` (642 works) came out of "Do
you know of any fics where…", and `tag:"or something"`, `tag:"I just"` and
`tag:"Shared past"` are the same thing.

What is true is that a phrase cannot be scored by its FIRST WORD. "time" is in
7% of summaries, so `Time Travel` keyed on "time" scores 0.03 and would be
thrown out — while it is one of the most useful tags in the index. Scored as
the whole phrase it is 4.32, because authors tag time travel far more often
than they narrate it.

So every tag is measured, and a tag of n words is counted against how often
that n-word run appears in a summary. The cost is generating 1-to-4 grams for
every sampled summary instead of splitting on spaces, which is about fifteen
seconds on top of a job that runs weekly.

What this is NOT
----------------
Not a ban and not a gate. It is a RANKING signal: a prose word sorts below the
terms that are actually attested, and is still offered, still searchable, and
still wins when it is the only thing a post says. A reader whose post says
`- dark` on a line of its own is taken at their word — the extractor already
ranks a line above loose prose, and this only reorders within the prose.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session, lift_statement_timeout  # noqa: E402

log = logging.getLogger("tag_prose")

# How much of the archive to read for the prose side. One percent is ~88,000
# summaries and takes about twenty seconds; the ratios it produces are stable
# to two significant figures against a five-percent run, and the threshold is
# nowhere near that fine.
SAMPLE_PERCENT = float(os.getenv("TAG_PROSE_SAMPLE_PCT", "1.0"))
SAMPLE_MAX = int(os.getenv("TAG_PROSE_SAMPLE_MAX", "120000"))
# The extractor's own floor. A tag below it is never chosen anyway.
MIN_WORKS = int(os.getenv("TAG_PROSE_MIN_WORKS", "50"))

# Below this, a word is English. Set well under the gap on purpose — see the
# module note: the two mistakes do not cost the same, so the cut is placed
# where it catches the measured accidents and nothing that is merely arguable.
PROSE_RATIO = float(os.getenv("TAG_PROSE_RATIO", "0.5"))

# A word has to be long enough to be worth measuring. Below three letters the
# tokeniser is mostly picking up initialisms, which this says nothing about.
_WORD = re.compile(r"[a-z']{3,}")

# How long a tag phrase can be and still be measured. The extractor only ever
# looks up 1-to-4 word runs, so a longer tag can never be a prose accident in
# the first place — it is only ever reached by an exact match on a line.
MAX_GRAM = 4

DDL = """
CREATE TABLE IF NOT EXISTS tag_prose (
    tag      text PRIMARY KEY,
    works    integer NOT NULL,
    written  integer NOT NULL,
    ratio    real    NOT NULL,
    built_at timestamp DEFAULT now()
)
"""


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
        lift_statement_timeout(db)
        total = int(db.execute(text(
            "SELECT count(*) FROM stories WHERE delisted_at IS NULL"
        )).scalar() or 0)

        # The vocabulary FIRST, so the summary scan can count only the phrases
        # that are actually tags. Counting every 1-to-4 gram in 87,000
        # summaries and then looking tags up in it builds a dictionary of tens
        # of millions of runs, almost all of them used once; counting against a
        # known vocabulary of 50,000 is the same answer in a tenth of the
        # memory.
        tags = db.execute(text("""
            SELECT value, max(count) FROM facets
             WHERE kind = 'tag' AND count >= :min
             GROUP BY 1
        """), {"min": MIN_WORKS}).fetchall()
        vocab = {}
        for value, works in tags:
            key = " ".join(_WORD.findall(value.lower()))
            if key:
                vocab.setdefault(key, []).append((value, works))
        widest = max((len(k.split()) for k in vocab), default=1)
        widest = min(widest, MAX_GRAM)

        seen: Counter = Counter()
        docs = 0
        rows = db.execute(text(f"""
            SELECT summary FROM stories TABLESAMPLE SYSTEM ({SAMPLE_PERCENT})
             WHERE summary IS NOT NULL AND length(summary) > 40
             LIMIT {SAMPLE_MAX}
        """)).fetchall()
        for (summary,) in rows:
            docs += 1
            words = _WORD.findall(summary.lower())
            # A SET per summary, so a summary that says "dark" six times is one
            # work that says dark. The comparison is against a tag, and a work
            # carries a tag once however often the word appears inside it.
            here = set()
            for n in range(1, widest + 1):
                for i in range(len(words) - n + 1):
                    g = " ".join(words[i:i + n])
                    if g in vocab:
                        here.add(g)
            for g in here:
                seen[g] += 1
        if docs < 1000:
            # A sample too small to divide by is not a reason to mark every
            # word in the index as English.
            log.warning("tag_prose: only %d summaries sampled, refusing to "
                        "rebuild on that", docs)
            return {"docs": docs, "tags": 0, "prose": 0}

        out = []
        for key, entries in vocab.items():
            value, works = max(entries, key=lambda e: e[1])
            written = seen.get(key, 0) / docs * total
            # A word the sample never saw is not English — it is a term of art
            # so specific that 88,000 summaries do not contain it, which is the
            # strongest possible version of the thing being measured.
            ratio = (works / written) if written >= 1 else float("inf")
            out.append((value, int(works), int(written), min(ratio, 9999.0)))

        prose = [r for r in out if r[3] < PROSE_RATIO]
        stats = {"docs": docs, "tags": len(out), "prose": len(prose)}

        if dry_run:
            log.info("tag_prose: %d tags measured, %d would be marked as "
                     "prose (from %d summaries)", len(out), len(prose), docs)
            for v, w, wr, r in sorted(prose, key=lambda x: -x[1])[:15]:
                log.info("  %-24s tagged %8d  written %8d  ratio %.2f",
                         v[:24], w, wr, r)
            return stats

        db.execute(text(DDL))
        # Replaced wholesale. Every number in it is derived from a vocabulary
        # and a corpus that both move, so a merge would keep a verdict nothing
        # supports any more.
        db.execute(text("DELETE FROM tag_prose"))
        for value, works, written, ratio in out:
            db.execute(text(
                "INSERT INTO tag_prose (tag, works, written, ratio) "
                "VALUES (:t, :w, :wr, :r)"),
                {"t": value, "w": works, "wr": written, "r": ratio})
        db.commit()

    log.info("tag_prose: %d tags measured, %d are English "
             "(from %d summaries)", stats["tags"], stats["prose"], stats["docs"])
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
