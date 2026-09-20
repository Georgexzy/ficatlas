"""What readers SAY, mapped to what the archives TAG.

    docker exec ficatlas-backend-1 python tag_hints.py --dry-run
    docker exec ficatlas-backend-1 python tag_hints.py

The problem
-----------
The extractor matches runs of a post against the tag vocabulary, which finds
words that ARE tags and nothing else. A reader who writes the tag's name gets a
good search; a reader who describes the same idea in their own words gets
nothing from it. Measured on real posts:

    "one where they pretend to be dating"     -> no tag  (Fake/Pretend Relationship)
    "he loses his memory and she finds him"   -> no tag  (Amnesia)
    "they hate each other at first"           -> no tag  (Enemies to Lovers)

Every one of those is a trope this index has tens of thousands of works for,
described in the words somebody would actually use.

Where the mapping comes from
----------------------------
It is not written down; it is already in the data. A work's SUMMARY is natural
language written by its author, and its TAGS are the structured version of the
same story — so the index holds millions of pairs of "how a person describes a
fic" and "what it gets tagged". The words that are disproportionately common in
the summaries of works carrying a tag are, near enough, the words a reader
would use to ask for it.

Lift, not frequency: how much likelier a word is in this tag's summaries than
in summaries generally. Measured on a 120,000-summary sample:

    Fake/Pretend Relationship   fake x160 · pretend x105 · dating x33
    Amnesia                     memory x31 · memories x25 · remember x12
    Enemies to Lovers           arrogant x39 · hatred x23 · tension x21
    Time Travel                 travel x32 · future x8 · fix x8

NAMES ARE THE NOISE
-------------------
The first run had `Hurt/Comfort` predicted by `kaveh`, `grian` and `suguru`,
and `Slow Burn` by `wheeler`, `byers` and `bakugo`. Those are characters, and
they score highly for the honest reason that the tag is popular in their
fandoms — which makes them excellent predictors of the tag and useless as
evidence about what a READER meant. A post saying "bakugo" wants Bakugou, not
slow burn.

So any word that is also a character, fandom or relationship facet is dropped,
which is the same filter the hub qualities use. What survives is vocabulary
about STORIES rather than about who is in them.

What this is NOT
----------------
Not a rewrite of the reader's request and not a replacement for the vocabulary
match, which is exact and should always win. This only ever fires on the words
the vocabulary could not place, and what it produces is OFFERED — the extractor
still probes it and still shows the reader what it searched for.
"""
from __future__ import annotations

import argparse
import collections
import logging
import os
import re
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session, lift_statement_timeout  # noqa: E402

log = logging.getLogger("tag_hints")

_WORD = re.compile(r"[a-z']{3,}")

# How much of the archive to read.
#
# Ten per cent, and the size is the whole feature working or not. At two per
# cent a tag like `Amnesia` drew about a hundred summaries, so almost no word
# cleared the lift floor and the table came out with 500 hints — enough to
# recognise "pretend to be dating" and not "loses his memory". The lift figures
# were already right at that size; there simply were not enough of them.
SAMPLE_PERCENT = float(os.getenv("TAG_HINTS_SAMPLE_PCT", "12.0"))
SAMPLE_MAX = int(os.getenv("TAG_HINTS_SAMPLE_MAX", "1200000"))
# Tags worth learning. Below this there are too few summaries to say anything,
# and the lift is dominated by whichever three works happen to be sampled.
MIN_TAG_WORKS = int(os.getenv("TAG_HINTS_MIN_TAG_WORKS", "120"))
MAX_TAGS = int(os.getenv("TAG_HINTS_MAX_TAGS", "4000"))
# A word must appear in this many of a tag's summaries before its lift means
# anything, and in this many overall before it is a word rather than a typo.
MIN_WORD_IN_TAG = int(os.getenv("TAG_HINTS_MIN_IN_TAG", "12"))
MIN_WORD_DOCS = int(os.getenv("TAG_HINTS_MIN_DOCS", "60"))
# The first pass only has to notice which grams are common, which a smaller
# sample answers just as well — and it is the pass whose counter cannot be
# bounded any other way.
VOCAB_SAMPLE_PCT = float(os.getenv("TAG_HINTS_VOCAB_PCT", "3.0"))
# Hard ceiling on that counter. Two builds were OOM-killed at 1.2GB before this
# existed, on a container that is also serving searches.
VOCAB_CAP = int(os.getenv("TAG_HINTS_VOCAB_CAP", "900000"))
# See `grams_of`. Needs headroom this box does not have.
BIGRAMS = os.getenv("TAG_HINTS_BIGRAMS", "false").lower() in ("1", "true", "yes")
# How much likelier the word has to be, and how many to keep per tag.
MIN_LIFT = float(os.getenv("TAG_HINTS_MIN_LIFT", "5.0"))
HINTS_PER_TAG = int(os.getenv("TAG_HINTS_PER_TAG", "30"))
# Past this many tags, a word is describing how summaries are WRITTEN rather
# than what any one of them is about. See the note where it is applied.
MAX_TAGS_PER_WORD = int(os.getenv("TAG_HINTS_MAX_TAGS_PER_WORD", "3"))

# Tags about the artefact or the author rather than the story. A reader asking
# for a trope never means any of these, so learning what their summaries say is
# learning noise. Same argument as hub_build.NOT_A_QUALITY.
NOT_A_TROPE = {
    "not beta read", "beta read", "no beta", "unbeta'd", "unbetaed",
    "author is bad at tagging", "bad at summaries", "tags will be added",
    "work in progress", "unfinished", "discontinued", "on hiatus",
    "cover art", "fanart", "art", "podfic", "translation", "crossposted",
    "originally posted on tumblr", "one shot", "oneshot", "drabble",
    "short", "ficlet", "my first fanfic", "first work",
}

# Tokenizer residue and words that carry no content. Contractions lose their
# tail to the word pattern — "didn't" leaves `didn` — and a hint on half a
# negation is worse than no hint.
_STOP = {
    "didn", "doesn", "couldn", "wouldn", "shouldn", "wasn", "weren", "isn",
    "aren", "hasn", "haven", "hadn", "don", "won", "can", "ain", "that",
    "this", "there", "their", "them", "then", "than", "with", "what", "when",
    "where", "which", "while", "would", "could", "should", "been", "being",
    "have", "here", "from", "into", "just", "like", "more", "most", "only",
    "over", "some", "such", "take", "than", "very", "well", "also", "back",
    "even", "much", "must", "never", "still", "thing", "things", "time",
    "actual", "information", "shit",
    # AUTHOR NOTES, not story. A summary routinely carries where else the work
    # is posted and how often it updates, and none of that is what the fic is
    # about.
    "wattpad", "https", "http", "updates", "update", "updated", "ongoing",
    "hiatus", "chapters", "chapter", "posted", "repost", "reposted",
    "discontinued", "rewrite", "rewritten", "edited", "sorry", "please",
    "enjoy", "review", "reviews", "comment", "comments", "kudos",
}

DDL = """
CREATE TABLE IF NOT EXISTS tag_hints (
    word     text NOT NULL,
    tag      text NOT NULL,
    lift     real NOT NULL,
    docs     integer NOT NULL,
    built_at timestamp DEFAULT now(),
    PRIMARY KEY (word, tag)
)
"""
DDL_INDEX = "CREATE INDEX IF NOT EXISTS ix_tag_hints_word ON tag_hints (word)"


def _name_words(db) -> set[str]:
    """Words that are really people or places. See the module note: these are
    the best predictors and the worst evidence."""
    out: set[str] = set()
    rows = db.execute(text("""
        SELECT value FROM facets
         WHERE kind IN ('character', 'fandom', 'fandom_ao3', 'relationship')
           AND count >= 40
    """)).fetchall()
    for (v,) in rows:
        for w in _WORD.findall(v.lower()):
            out.add(w)
    return out


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
        lift_statement_timeout(db)

        wanted = {r[0] for r in db.execute(text("""
            SELECT value FROM facets
             WHERE kind = 'tag' AND count >= :m
             ORDER BY count DESC LIMIT :lim
        """), {"m": MIN_TAG_WORKS, "lim": MAX_TAGS}).fetchall()}
        names = _name_words(db)
        log.info("tag_hints: %d tags to learn, %d name-words excluded",
                 len(wanted), len(names))

        # TWO PASSES, because one does not fit in memory.
        #
        # Bigrams multiply the vocabulary by roughly forty, and counting them
        # per tag at the same time as counting them overall needs both a
        # vocabulary-sized map AND a tags x vocabulary map. The single-pass
        # version was OOM-killed at 1.2GB on an 827,000-summary sample.
        #
        # So: learn WHICH grams are common enough to matter (bounded, prunable,
        # one number each), then count only those against the tags. Two scans
        # of a sample this size is about four minutes, on a job that runs
        # weekly.
        def _scan(pct: float, cap: int):
            return text(f"""
                SELECT summary, tags FROM stories TABLESAMPLE SYSTEM ({pct})
                 WHERE summary IS NOT NULL AND length(summary) > 60
                   AND tags IS NOT NULL AND cardinality(tags) > 0
                 LIMIT {cap}
            """)

        VOCAB_SCAN = _scan(VOCAB_SAMPLE_PCT, SAMPLE_MAX // 4)
        SCAN = _scan(SAMPLE_PERCENT, SAMPLE_MAX)

        def grams_of(summary: str) -> set[str]:
            toks = [w for w in _WORD.findall(summary.lower()) if w not in names]
            out = set(toks)
            # PAIRS ARE OFF, and this is the honest limit of the feature.
            #
            # The meaning of a request is often in the pair — "time loop",
            # "one bed", "hate each other" — and a unigram model cannot reach
            # any of them. Mining pairs was tried properly and does not fit:
            # bigrams multiply the vocabulary about fortyfold, two builds were
            # OOM-killed at 1.2GB on a container that is also serving searches,
            # and the bounded version that survived kept twenty-one of them
            # because a rare gram is the first thing a memory cap discards.
            #
            # What survives without them is the class where a reader's own word
            # is close to the tag's: arranged marriage, fake dating, pining,
            # soulmates, amnesia, touch-starved. That is a real and useful
            # class, and it is the whole of what this table claims.
            #
            # Turning them on needs more memory than this box has spare, not a
            # cleverer filter — so it is a flag rather than a deletion.
            if BIGRAMS:
                out.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
            return out

        # Pass one runs on a SMALLER sample than pass two, deliberately.
        # Finding which grams are common does not need the whole sample — a
        # gram frequent enough to matter is frequent in a tenth of it — and the
        # counter here is the one that cannot be bounded any other way.
        docfreq = collections.Counter()
        docs = 0
        floor = 2
        for summary, _tags in db.execute(VOCAB_SCAN):
            docs += 1
            docfreq.update(grams_of(summary))
            # BOUNDED BY SIZE, not by a document count. Pruning every N
            # documents still lets the peak between prunes grow with the
            # sample, and that peak is what the OOM killer sees: two attempts
            # died at 1.2GB, one of them after the prune interval had already
            # been tightened. Cap the map instead and raise the floor until it
            # fits, which bounds memory whatever the sample size.
            if len(docfreq) > VOCAB_CAP:
                for k, v in list(docfreq.items()):
                    if v <= floor:
                        del docfreq[k]
                if len(docfreq) > VOCAB_CAP * 0.9:
                    floor += 1
        # The vocabulary pass sees a FRACTION of the documents the counting
        # pass will, so a gram that needs MIN_WORD_DOCS there needs
        # proportionally fewer here. Getting this the wrong way round asks for
        # four times as many instead of a quarter, and the table comes out with
        # 581 hints instead of thousands — which looks like a tuning problem
        # and is arithmetic.
        scale = VOCAB_SAMPLE_PCT / SAMPLE_PERCENT
        vocab = {w for w, c in docfreq.items()
                 if c >= max(3, MIN_WORD_DOCS * scale)}
        del docfreq
        log.info("tag_hints: %d grams worth counting, from %d summaries",
                 len(vocab), docs)

        pertag: dict[str, collections.Counter] = collections.defaultdict(
            collections.Counter)
        ntag = collections.Counter()
        docs = 0
        docfreq = collections.Counter()
        for summary, tags in db.execute(SCAN):
            docs += 1
            keep = grams_of(summary) & vocab
            if not keep:
                continue
            docfreq.update(keep)
            for t in (set(tags) & wanted):
                ntag[t] += 1
                pertag[t].update(keep)

        if docs < 5_000:
            log.warning("tag_hints: only %d summaries sampled, refusing to "
                        "build on that", docs)
            return {"docs": docs, "tags": 0, "hints": 0}

        raw: list[tuple[str, str, float, int]] = []
        for tag, counts in pertag.items():
            if ntag[tag] < 30 or tag.lower() in NOT_A_TROPE:
                continue
            scored = []
            for w, c in counts.items():
                if c < MIN_WORD_IN_TAG or docfreq[w] < MIN_WORD_DOCS:
                    continue
                if w in _STOP:
                    continue
                lift = (c / ntag[tag]) / (docfreq[w] / docs)
                if lift >= MIN_LIFT:
                    scored.append((lift, w, c))
            scored.sort(reverse=True)
            for lift, w, c in scored[:HINTS_PER_TAG]:
                raw.append((w, tag, float(lift), int(c)))

        # EACH WORD KEEPS THE TAGS IT IS BEST EVIDENCE FOR, and drops the rest.
        #
        # The first build had `navigate` as a hint for Friends to Lovers, Found
        # Family, Slow Burn AND Romance — how people write summaries, not a
        # trope signal, and the fic-blurb equivalent of the prose words
        # `tag_prose` keeps out of a query.
        #
        # The obvious cut — drop any word that predicts more than N tags — was
        # tried and is wrong, because it throws away the best signals in the
        # set: `pining` predicts Mutual Pining, Pining AND Getting Together,
        # and it is right about all three. `fake` and `memory` went the same
        # way. What separates them from `navigate` is not how MANY tags they
        # point at but how HARD: 160x against 6x.
        #
        # So the ranking is per word, keeping only the tags it is strongest
        # evidence for. A cliché scores about the same everywhere and keeps its
        # three weakest-looking claims; a real signal keeps the ones that made
        # it a signal.
        by_word: dict[str, list] = collections.defaultdict(list)
        for w, t, lift, c in raw:
            by_word[w].append((lift, t, c))
        hints = []
        for w, entries in by_word.items():
            entries.sort(reverse=True)
            for lift, t, c in entries[:MAX_TAGS_PER_WORD]:
                hints.append((w, t, lift, c))

        stats = {"docs": docs, "tags": len(pertag), "hints": len(hints)}
        if dry_run:
            log.info("tag_hints: %d hints across %d tags from %d summaries",
                     len(hints), len({h[1] for h in hints}), docs)
            by_tag: dict[str, list] = collections.defaultdict(list)
            for w, t, lift, c in hints:
                by_tag[t].append((lift, w))
            for t in sorted(by_tag, key=lambda k: -len(by_tag[k]))[:12]:
                ws = " · ".join(f"{w}×{l:.0f}"
                                for l, w in sorted(by_tag[t], reverse=True)[:7])
                log.info("  %-34s %s", t[:34], ws)
            return stats

        db.execute(text(DDL))
        db.execute(text("DELETE FROM tag_hints"))
        for w, t, lift, c in hints:
            db.execute(text(
                "INSERT INTO tag_hints (word, tag, lift, docs) "
                "VALUES (:w, :t, :l, :c) ON CONFLICT DO NOTHING"),
                {"w": w, "t": t, "l": lift, "c": c})
        db.execute(text(DDL_INDEX))
        db.commit()

    log.info("tag_hints: %d hints across %d tags from %d summaries",
             stats["hints"], stats["tags"], stats["docs"])
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
