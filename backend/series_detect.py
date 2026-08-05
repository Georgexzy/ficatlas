r"""
Find the series hiding in the index, across every site.
=======================================================

AO3 has series as a first-class thing. FanFiction.net and FictionAlley never
did — authors there signal it in the title ("Living with Danger", "Living
without Danger", "Dealing with Danger") or in the summary ("Sequel to X", "Book
2 of the Y series") or not at all. And none of our bulk dumps carries a series
field from any source, AO3 included. So every grouping here is derived, and the
question is how to derive one without inventing it.

Two signals, in order of how much they can be trusted:

  1. AN EXPLICIT STATEMENT in the summary. "Sequel to Living with Danger",
     "Part 3 of the Dangerverse". The author said so; we are reading, not
     guessing. These also give the ORDER, which is the whole point of a series
     — a grouping with no reading order is just a tag.

  2. A SHARED DISTINCTIVE WORD in the titles of one author's works. This is how
     the Dangerverse actually presents itself, and how most FFN series do.

The second needs care, and the naive version is wrong. whydoyouneedtoknow has
five titles containing "danger" — correct — and three containing "return"
(Return Receipt, Return for Repairs, The Point of No Return) which are unrelated.
Both look identical to a rule that just counts shared words.

What separates them is how ordinary the word is. Measured against 300k sampled
titles: "return" appears in 369, "danger" in 74, "dudes" in 10. So tokens are
weighted by inverse document frequency and a group must clear a threshold on the
RAREST word it shares — a series named after a common word needs more members to
be believed, and one named after "Dangerverse" needs almost none.

Ordering
--------
Explicit position where the summary states one. Otherwise the site's own work
id, ascending — AO3 and FanFiction.net both hand out increasing ids, so the id
IS publication order and cannot disagree with itself. published_at can and does:
"Living with Danger" carries 2026-06-12 against site id 2109424, the oldest in
its own series, which sorted the first book of the Dangerverse last.

    docker compose exec backend python series_detect.py --dry-run --author whydoyouneedtoknow
    docker compose exec backend python series_detect.py --limit-authors 5000
"""

import argparse
import logging
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

from sqlalchemy import text as sql_text

import series_cues
from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Words that carry no identity. Deliberately short: the IDF weighting below
# already handles ordinary words, and a long stop-list would start removing the
# very words some series are named after ("Dark", "Blood", "Wolf").
STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "without", "from", "by", "part", "book", "chapter", "vol", "volume", "series",
    "one", "two", "three", "four", "five", "new", "old", "not", "his", "her",
    "their", "you", "your", "my", "me", "is", "it", "as", "but", "that", "this",
    "what", "when", "who", "why", "how", "all", "no", "up", "out", "into", "über",
}

# "Sequel to X", "Part 3 of the Y series" — the author telling us directly.
_SEQUEL_RE = re.compile(
    r"\b(?:sequel|prequel|follow[- ]?up|companion(?:\s+piece)?)\s+to\s+[\"'“]?([^.\"'”\n]{3,80})",
    re.I)
_PART_RE = re.compile(
    r"\b(?:part|book|story|installment|instalment)\s+(\d{1,2}|[ivx]{1,5})\b"
    r"(?:\s+(?:of|in)\s+(?:the\s+)?[\"'“]?([^.\"'”\n]{3,60}))?", re.I)

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10}

# A group must reach this to be recorded. The score is the summed IDF of the
# words the titles share, so three works sharing something ordinary can fail
# while two sharing something distinctive pass.
# Tuned against a known case. whydoyouneedtoknow's Dangerverse — five real
# books — scores 40.6, while "Return Receipt / Return for Repairs / The Point of
# No Return", three unrelated works sharing an ordinary word, scores 19.7. The
# threshold sits above the second.
#
# Deliberately biased toward missing series rather than inventing them. A
# grouping that is wrong does not merely fail to help: it tells a reader that
# unrelated works are a sequence and puts them in an order, which is worse than
# saying nothing.
MIN_SCORE = float(os.getenv("SERIES_MIN_SCORE", "25.0"))
MIN_WORKS = int(os.getenv("SERIES_MIN_WORKS", "3"))

# The shared word must be distinctive ON ITS OWN, not merely distinctive-times-
# popular. The score is idf x member count, so an ordinary word clears any
# threshold once enough works share it — which is precisely the false-positive
# shape. Sampling the first real run turned up a "Love series" of seven and a
# "Like series" of six, both nonsense, sitting beside correct ones.
#
# Measured idf, from 300k sampled titles:
#     love 3.60   like 4.70   potter 6.08      <- all wrong
#     danger 8.22   insomnia 8.25   bodyguard 9.12
#     holder 11.51   ghostober 12.61           <- all right
# The floor sits in the gap. "potter" landing on the wrong side is correct too:
# it is a fandom word, not a series name.
MIN_TOKEN_IDF = float(os.getenv("SERIES_MIN_TOKEN_IDF", "7.0"))

# An inferred series of a hundred works is not a series.
#
# The first clean run produced "Oneshots series" with 103 members, "Tong" with
# 101, "Backstage" with 89 — one author's whole output sharing a word, which is
# what a habit looks like, not a sequence. Real inferred series in this data run
# to five or ten; the Dangerverse is five.
#
# Not applied to STATED or EXPLICIT series: an author who says "part 40 of X",
# or AO3 reporting its own 43-work series, is stating a fact and the size is
# theirs to decide.
MAX_INFERRED_WORKS = int(os.getenv("SERIES_MAX_INFERRED", "25"))


def tokens(title: str) -> set[str]:
    t = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    return {w for w in t.split() if len(w) > 3 and w not in STOP}


def parse_position(summary: str | None) -> int | None:
    if not summary:
        return None
    m = _PART_RE.search(summary)
    if not m:
        return None
    raw = m.group(1).lower()
    return int(raw) if raw.isdigit() else _ROMAN.get(raw)


def build_idf(db, sample: int = 300_000) -> dict[str, float]:
    """How ordinary each title word is, from a sample of the corpus.

    Sampled rather than counted: an exact frequency over 19.7M titles costs
    minutes and would not move the answer, because this only needs to separate
    "appears in 74 titles" from "appears in 369".
    """
    import math
    rows = db.execute(sql_text(
        f"SELECT title FROM stories TABLESAMPLE SYSTEM_ROWS({sample}) "
        f"WHERE title IS NOT NULL")).fetchall()
    freq: dict[str, int] = defaultdict(int)
    for (title,) in rows:
        for tok in tokens(title):
            freq[tok] += 1
    n = max(1, len(rows))
    # Unseen words get the highest weight the table can express, which is right:
    # a word that did not appear in 300,000 titles is as distinctive as it gets.
    return {w: math.log(n / c) for w, c in freq.items()}


def subject_words(works: list[dict]) -> set[str]:
    """Words that name what the works are ABOUT rather than which series it is.

    The dominant false positive, found by sampling the first real run: an author
    who writes several Star Trek stories gets a "Trek series", and one who
    writes about Aeryn gets an "Aeryn series". The shared word is real and the
    grouping is wrong — it is the fandom or a character, which every work by
    that author naturally shares.

    Taken from the works' own fandom, character and relationship tags, so it
    adapts per author and per fandom with nothing hard-coded. A word is only
    excluded when it appears in the tags of MORE THAN ONE of the works: a series
    genuinely named after its protagonist ("the Harry Potter series") would
    otherwise be thrown away on the strength of one tag.
    """
    from collections import Counter
    seen: Counter = Counter()
    for w in works:
        words: set[str] = set()
        for value in w.get("subject") or []:
            words |= tokens(value)
        for word in words:
            seen[word] += 1
    return {word for word, n in seen.items() if n > 1}


def group_author(works: list[dict], idf: dict[str, float], default_idf: float
                 ) -> list[tuple[str, list[dict], float]]:
    """Series candidates among one author's works: (name, members, score)."""
    subjects = subject_words(works)
    by_token: dict[str, list[dict]] = defaultdict(list)
    for w in works:
        for tok in tokens(w["title"]):
            if tok in subjects:
                continue
            by_token[tok].append(w)

    out: list[tuple[str, list[dict], float]] = []
    used: set[str] = set()
    # Strongest first, so a work joins the most distinctive series it fits
    # rather than the first one examined.
    for tok, members in sorted(
            by_token.items(),
            key=lambda kv: -(idf.get(kv[0], default_idf) * len(kv[1]))):
        members = [m for m in members if m["id"] not in used]
        if len(members) < MIN_WORKS:
            continue
        tok_idf = idf.get(tok, default_idf)
        if tok_idf < MIN_TOKEN_IDF:
            continue
        if len(members) > MAX_INFERRED_WORKS:
            continue
        score = tok_idf * len(members)
        if score < MIN_SCORE:
            continue
        for m in members:
            used.add(m["id"])
        out.append((tok, members, score))
    return out


def series_name(token: str, members: list[dict]) -> str:
    """A name a reader would recognise.

    The shared word alone reads badly ("danger"), so the longest common run of
    words is preferred where there is one, and the capitalisation is taken from
    a real title rather than invented.
    """
    for m in members:
        for word in re.findall(r"[A-Za-z0-9']+", m["title"] or ""):
            if word.lower() == token:
                return f"{word} series"
    return f"{token.title()} series"


def run(dry_run: bool, only_author: str | None, limit_authors: int,
        offset: int = 0) -> int:
    with db_session() as db:
        log.info("measuring how ordinary each title word is…")
        idf = build_idf(db)
        default_idf = max(idf.values()) if idf else 12.0
        log.info(f"  {len(idf):,} distinct title words sampled")

        if only_author:
            # Lowercased to match the query above, which no longer folds it.
            authors = [only_author.strip().lower()]
        else:
            # Only authors with enough works to have a series at all.
            # Grouped on lower(author), which is the indexed expression, and
            # WITHOUT an ORDER BY count DESC.
            #
            # The obvious form — GROUP BY author ORDER BY count(*) DESC — has to
            # aggregate all 19.7M rows and then sort the result, which took 21s
            # on an idle database and over SIXTY under load. That is the
            # statement_timeout added for reliability, so the pass died on its
            # very first query every time it ran, silently, before examining a
            # single author. Grouping on the indexed expression instead is an
            # index-only scan: 414ms.
            #
            # Losing "biggest authors first" costs nothing here. Every author
            # gets examined eventually and the order they are examined in does
            # not change what is found.
            authors = [r[0] for r in db.execute(sql_text("""
                SELECT lower(author) FROM stories
                WHERE author IS NOT NULL AND author <> '' AND author <> 'Unknown'
                GROUP BY lower(author) HAVING count(*) BETWEEN 2 AND 400
                -- Ordered so OFFSET means something across runs. Grouping is
                -- already an index-only scan, and ordering its output is cheap
                -- next to it — without this the background loop would re-walk
                -- an arbitrary slice every pass and never reach the tail.
                ORDER BY lower(author)
                OFFSET :off LIMIT :lim
            """), {"lim": limit_authors, "off": offset}).fetchall()]

        log.info(f"examining {len(authors):,} authors")
        found = stored = 0

        for i, author in enumerate(authors, 1):
            rows = db.execute(sql_text("""
                SELECT id, title, summary, site, published_at,
                       CASE WHEN site_id ~ '^[0-9]+$'
                            THEN site_id::bigint ELSE NULL END AS numeric_id,
                       fandoms, characters, relationships
                FROM stories
                -- lower(author) = :a, with the PARAMETER already lowercased by
                -- the caller — not author = :a, and not lower(author) =
                -- lower(:a). Three forms, three different outcomes:
                --   author = :a           misses the index (it is on
                --                         lower(author)) AND, now that the
                --                         caller lowercases, matches nothing
                --                         for any author with a capital letter.
                --   lower(author)=lower(:a)  Postgres cannot fold lower($1) in a
                --                         generic prepared plan, so it stops
                --                         using the index too: ~6s per author.
                --   lower(author) = :a    an 8ms index scan.
                WHERE lower(author) = :a AND title IS NOT NULL AND delisted_at IS NULL
            """), {"a": author}).fetchall()
            works = [{"id": str(r[0]), "title": r[1], "summary": r[2],
                      "site": (r[3].value if hasattr(r[3], "value") else str(r[3])),
                      "published_at": r[4], "numeric_id": r[5],
                      "subject": (r[6] or []) + (r[7] or []) + (r[8] or [])}
                     for r in rows]
            if len(works) < MIN_WORKS:
                continue

            # Precedence: what the author SAID beats what the titles rhyme
            # with. Title similarity is the last resort, used only on works no
            # stated cue has already accounted for.
            groups: list[tuple[str, list[dict], float, str]] = []
            claimed: set[str] = set()

            # 1. A named series stated in the summary — "Third in the Facing the
            #    Future series". Gives the name AND the position; nothing is
            #    guessed, only parsed.
            named: dict[str, list[dict]] = defaultdict(list)
            for w in works:
                cue = series_cues.parse_named(w.get("summary"))
                if cue:
                    w["cue_pos"] = cue["position"]
                    named[cue["name"]].append(w)
            for name, members in named.items():
                if len(members) < 2:
                    continue
                groups.append((name, members, 30.0, "stated"))
                claimed.update(m["id"] for m in members)

            # 2. "Sequel to X" chains, resolved against this author's own works.
            rest = [w for w in works if w["id"] not in claimed]
            for chain in series_cues.link_by_relatives(rest):
                first = min(chain, key=lambda m: (m["numeric_id"] is None,
                                                  m["numeric_id"]))
                groups.append((f"{first['title']} series", chain, 28.0, "stated"))
                claimed.update(m["id"] for m in chain)

            # 3. Distinctive shared title words, on whatever is left.
            rest = [w for w in works if w["id"] not in claimed]
            for token, members, score in group_author(rest, idf, default_idf):
                # Prefer the name the author uses. The titles are what FOUND the
                # group; the summaries often say what it is CALLED, and the two
                # are not the same — "danger" found the Dangerverse, and
                # "Dangerverse" is its name.
                stated = series_cues.stated_name([m.get("summary") for m in members])
                groups.append((stated or series_name(token, members),
                               members, score, "inferred"))

            for name, members, score, source in groups:
                found += 1
                # Author-stated position wins; otherwise publication order, which
                # is AO3's own fallback for a series with no positions set.
                for m in members:
                    m["pos"] = m.get("cue_pos") or parse_position(m.get("summary"))
                    # Main sequence or companion piece.
                    #
                    # The author stating a position — "third in the Dangerverse"
                    # — is them placing the work IN the sequence. A work that
                    # only mentions the series name, or says outright that it is
                    # a side story, is not part of that run. Measured on the
                    # Dangerverse: the five with stated ordinals are 215k-520k
                    # words, the five without are 1.8k-49k.
                    rels = series_cues.parse_relative(m.get("summary"))
                    is_side = any(r["kind"] in ("side-story", "companion",
                                                "companion-piece")
                                  for r in rels)
                    m["role"] = "side" if (is_side or m["pos"] is None) else "main"
                # Publication order only for the main run; a side story has no
                # place in a numbered sequence it was never part of.
                mains = [m for m in members if m.get("role") != "side"]
                if mains and all(m["pos"] is None for m in mains):
                    members_for_order = mains
                else:
                    members_for_order = members
                if all(m["pos"] is None for m in members):
                    # Site id, not published_at. AO3 and FF.net both assign
                    # increasing work ids, so the id IS publication order and it
                    # cannot be wrong — while published_at demonstrably can be:
                    # "Living with Danger" carries 2026-06-12 against a site id
                    # of 2109424, the oldest in its own series, which sorted the
                    # first book of the Dangerverse last.
                    members.sort(key=lambda m: (
                        m["numeric_id"] is None, m["numeric_id"],
                        m["published_at"] is None, m["published_at"]))
                    for n, m in enumerate(members, 1):
                        m["pos"] = n
                if dry_run:
                    log.info(f"  {author} — {name} (score {score:.1f})")
                    for m in sorted(members, key=lambda m: (m["pos"] or 99)):
                        log.info(f"      {m['pos']}. {m['title'][:64]}")
                    continue

                sid = db.execute(sql_text("""
                    INSERT INTO series (name, author, site, source, confidence, work_count)
                    VALUES (:n, :a, :s, :src, :c, :w)
                    ON CONFLICT (lower(coalesce(author,'')), lower(name)) DO UPDATE
                        SET work_count = EXCLUDED.work_count,
                            confidence = EXCLUDED.confidence
                    RETURNING id
                """), {"n": name, "a": author, "s": members[0]["site"],
                       "src": source, "c": min(1.0, score / 30.0),
                       "w": len(members)}).scalar()
                for m in members:
                    db.execute(sql_text("""
                        INSERT INTO series_works (series_id, story_id, position, role)
                        VALUES (:s, :w, :p, :r)
                        ON CONFLICT (series_id, story_id) DO UPDATE
                            SET position = EXCLUDED.position, role = EXCLUDED.role
                    """), {"s": sid, "w": m["id"], "p": m["pos"],
                           "r": m.get("role", "main")})
                stored += 1

            if not dry_run and i % 200 == 0:
                db.commit()
                log.info(f"  {i:,}/{len(authors):,} authors · {stored:,} series")

        if not dry_run:
            db.commit()
            # Better evidence supersedes weaker. Once an author's series is
            # known from a stated cue or from AO3 itself, an inferred grouping
            # of the same works is a duplicate under a name we invented —
            # "Danger series" sitting beside "Dangerverse", describing the
            # same books. Dropped rather than left for a reader to reconcile.
            gone = db.execute(sql_text("""
                DELETE FROM series inf
                WHERE inf.source = 'inferred'
                  AND EXISTS (
                    SELECT 1 FROM series better
                    WHERE better.source IN ('stated','explicit')
                      AND lower(coalesce(better.author,'')) = lower(coalesce(inf.author,''))
                      AND better.id <> inf.id
                      AND NOT EXISTS (
                        SELECT 1 FROM series_works iw
                        WHERE iw.series_id = inf.id
                          AND NOT EXISTS (
                            SELECT 1 FROM series_works bw
                            WHERE bw.series_id = better.id AND bw.story_id = iw.story_id))
                  )
            """)).rowcount
            db.commit()
            if gone:
                log.info(f"  dropped {gone:,} inferred series superseded by a stated one")
        log.info(f"DONE — {found:,} candidate series, {stored:,} stored")
    return len(authors)


def main() -> int:
    ap = argparse.ArgumentParser(description="Group works into series")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--author", default=None)
    ap.add_argument("--limit-authors", type=int, default=20000)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the first N authors (the background loop walks these)")
    args = ap.parse_args()
    return run(args.dry_run, args.author, args.limit_authors, args.offset)


if __name__ == "__main__":
    sys.exit(main())
