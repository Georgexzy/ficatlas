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


def group_author(works: list[dict], idf: dict[str, float], default_idf: float
                 ) -> list[tuple[str, list[dict], float]]:
    """Series candidates among one author's works: (name, members, score)."""
    by_token: dict[str, list[dict]] = defaultdict(list)
    for w in works:
        for tok in tokens(w["title"]):
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
        score = idf.get(tok, default_idf) * len(members)
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


def run(dry_run: bool, only_author: str | None, limit_authors: int) -> int:
    with db_session() as db:
        log.info("measuring how ordinary each title word is…")
        idf = build_idf(db)
        default_idf = max(idf.values()) if idf else 12.0
        log.info(f"  {len(idf):,} distinct title words sampled")

        if only_author:
            authors = [only_author]
        else:
            # Only authors with enough works to have a series at all.
            authors = [r[0] for r in db.execute(sql_text("""
                SELECT author FROM stories
                WHERE author IS NOT NULL AND author <> '' AND author <> 'Unknown'
                GROUP BY author HAVING count(*) BETWEEN 2 AND 400
                ORDER BY count(*) DESC LIMIT :lim
            """), {"lim": limit_authors}).fetchall()]

        log.info(f"examining {len(authors):,} authors")
        found = stored = 0

        for i, author in enumerate(authors, 1):
            rows = db.execute(sql_text("""
                SELECT id, title, summary, site, published_at,
                       CASE WHEN site_id ~ '^[0-9]+$'
                            THEN site_id::bigint ELSE NULL END AS numeric_id
                FROM stories
                WHERE author = :a AND title IS NOT NULL AND delisted_at IS NULL
            """), {"a": author}).fetchall()
            works = [{"id": str(r[0]), "title": r[1], "summary": r[2],
                      "site": (r[3].value if hasattr(r[3], "value") else str(r[3])),
                      "published_at": r[4], "numeric_id": r[5]} for r in rows]
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
                        INSERT INTO series_works (series_id, story_id, position)
                        VALUES (:s, :w, :p)
                        ON CONFLICT (series_id, story_id) DO UPDATE
                            SET position = EXCLUDED.position
                    """), {"s": sid, "w": m["id"], "p": m["pos"]})
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
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Group works into series")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--author", default=None)
    ap.add_argument("--limit-authors", type=int, default=20000)
    args = ap.parse_args()
    return run(args.dry_run, args.author, args.limit_authors)


if __name__ == "__main__":
    sys.exit(main())
