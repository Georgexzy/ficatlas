"""Series that the author declared in prose, because the archive had no field.

Why the title matcher cannot find these
---------------------------------------
The existing detector (series_detect.py) groups an author's works by shared
title words, which is the right signal for "Dangerverse Book 1" and useless for
the case that prompted this. Lightning on the Wave's Sacrifices Arc is seven
works called:

    Saving Connor · No Mouth But Some Serpent's · Maze of Light
    Freedom And Not Peace · Wind That Shakes the Seas and Stars
    A Song In Time of Revolution · I Am Also Thy Brother

Not one word in common. The title matcher would never have found it, whenever
the sweep reached the letter L — and the summaries say it outright:

    "AU, short story set in my Sacrifices universe."
    "AU, part 7 of Sacrifices."

FanFiction.net has no series feature at all, so its authors have always written
this into the summary by hand. That is where the signal is.

Why this is conservative
------------------------
A wrong grouping is worse than no grouping: it puts unrelated works under one
name and tells a reader they are reading a sequence that does not exist. So only
explicit, positional declarations count — "part 3 of X", "book two of X",
"sequel to X" — and the extracted name is then required to agree across an
author's works before anything is stored. A single work saying "part 2 of
something" produces nothing on its own.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Words that are never a series name on their own. Extracted names are matched
# against this AFTER normalisation, so "the series" and "The Series" both go.
_JUNK_NAMES = {
    "series", "the series", "this series", "a series", "my series", "trilogy",
    "the trilogy", "saga", "the saga", "story", "the story", "this story",
    "my story", "it", "this", "that", "these", "those", "them", "sequel",
    "the sequel", "prequel", "the prequel", "part", "the same", "same",
    "which", "what", "and", "canon", "the book", "book", "the books", "books",
    "the first", "the second", "the third", "arc", "the arc", "verse", "au",
    # Names that describe the medium rather than a work. An author writing
    # "part 2 of my Harry Potter fanfiction" has named a fandom and a format,
    # not a series, and grouping on it files unrelated works together.
    "fanfiction", "harry potter fanfiction", "fanfic", "fic", "fics",
    "fic reqs", "requests", "oneshots", "one shots", "one-shots", "drabbles",
    "shorts", "collection", "my fics", "my stories", "my work", "my works",
}

_ORDINALS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_NUM = r"(?:\d{1,2}|" + "|".join(_ORDINALS) + r")"

# Positional declarations, which are the only ones strong enough to act on.
#
#   "part 7 of Sacrifices"        -> (Sacrifices, 7)
#   "Book Two of the Dangerverse" -> (the Dangerverse, 2)
#   "#3 in the Chronicles"        -> (Chronicles, 3)
#
# The name runs to a sentence boundary or a closing bracket, because a summary
# continues past it: "part 7 of Sacrifices. In the wake of death and disaster,"
_PATTERNS = [
    re.compile(r"\b(?:part|book|story|instal?lment|entry|volume|vol\.?)\s*"
               r"(?P<num>" + _NUM + r")\s+(?:of|in)\s+(?:the\s+)?"
               r"(?P<name>[^.;:!?\)\]\n]{2,60})", re.IGNORECASE),
    re.compile(r"#\s*(?P<num>\d{1,2})\s+(?:of|in)\s+(?:the\s+)?"
               r"(?P<name>[^.;:!?\)\]\n]{2,60})", re.IGNORECASE),
    re.compile(r"\b(?P<num>" + _NUM + r")(?:st|nd|rd|th)?\s+"
               r"(?:story|book|part|instal?lment)\s+(?:of|in)\s+(?:the\s+)?"
               r"(?P<name>[^.;:!?\)\]\n]{2,60})", re.IGNORECASE),
]

# Trailing words that belong to the sentence rather than to the name.
_TRAILING = re.compile(
    r"\s+(?:series|trilogy|saga|universe|verse|arc|sequence|collection)$",
    re.IGNORECASE)


def _clean_name(raw: str) -> str:
    name = raw.strip().strip("\"'“”‘’ ")
    # "the Sacrifices series" and "Sacrifices" are the same series.
    name = _TRAILING.sub("", name).strip()
    name = re.sub(r"^(?:the|my|a)\s+", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,-–—:")


def _num_value(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        n = int(raw)
        # A "part 40 of" is far more likely to be a chapter reference than a
        # series position, and nothing sane declares a 40-book series inline.
        return n if 1 <= n <= 30 else None
    return _ORDINALS.get(raw)


def extract_declaration(summary: str | None) -> tuple[str, int] | None:
    """Pull an explicit "part N of X" declaration out of a summary.

    Returns (series_name, position), or None when the summary does not make a
    positional claim. Deliberately returns nothing for vaguer phrasings like
    "sequel to X" or "set in my X universe": those identify a relationship
    without a position, and acting on them alone produced groupings that read as
    confident and were not.
    """
    if not summary:
        return None
    for pattern in _PATTERNS:
        m = pattern.search(summary)
        if not m:
            continue
        pos = _num_value(m.group("num"))
        name = _clean_name(m.group("name"))
        if pos is None or len(name) < 3:
            continue
        if name.lower() in _JUNK_NAMES:
            continue
        # A name that is all digits or punctuation is a false read of the
        # sentence, not a title.
        if not re.search(r"[A-Za-z]{3}", name):
            continue
        return name, pos
    return None


def mentions_series(summary: str | None, name: str) -> bool:
    """Does this summary name a series we already know the author has?

    The weak half of the pair, and only ever used to EXTEND a series that a
    positional declaration has already established for the same author.

    It exists because the case that prompted all this is not fully solvable
    otherwise. Of Lightning on the Wave's seven Sacrifices works, exactly one
    gives a position — "part 7 of Sacrifices" — while another says only "set in
    my Sacrifices universe", and the rest name the canon book they diverge from
    rather than the series. A positional-only rule finds a series of one and
    therefore stores nothing.

    On its own this phrasing is far too loose to group on: "set in my X
    universe" would happily match a fandom name, a ship, or a common noun. Tied
    to a name an author has already declared positionally, and restricted to
    that author's own works, it is safe.
    """
    if not summary or not name or len(name) < 4:
        return False
    pattern = re.compile(
        r"(?:sequel to|prequel to|companion (?:to|piece to)|set in|part of|in the|from the)?"
        r"\s*\b" + re.escape(name) + r"\b"
        r"\s*(?:series|universe|verse|arc|saga|trilogy|world|continuity)",
        re.IGNORECASE)
    return bool(pattern.search(summary))


def group_by_declaration(works: list[dict]) -> dict[str, list[dict]]:
    """Group one author's works by the series their summaries declare.

    `works` are dicts with at least `id`, `title` and `summary`. Returns
    {series_name: [work, ...]}, members in reading order.

    A series needs at least one POSITIONAL declaration to exist at all — that is
    what names it and proves the author thinks of it as a sequence. It then
    needs a second member from either source: another positional declaration, or
    a work of the same author's that names the series without placing itself in
    it. One work claiming to be "part 2 of something" tells us a series exists
    but not what else is in it, and a one-member series helps nobody.

    That two-source rule is what makes the case this was built for work at all.
    Lightning on the Wave's Sacrifices Arc gives exactly ONE position across
    seven works ("part 7 of Sacrifices"); a second says only "set in my
    Sacrifices universe". Positional-only would find a series of one and store
    nothing.
    """
    positional: dict[str, list[dict]] = defaultdict(list)
    for w in works:
        found = extract_declaration(w.get("summary"))
        if not found:
            continue
        name, pos = found
        positional[name.lower()].append({**w, "series_name": name, "position": pos})

    out: dict[str, list[dict]] = {}
    claimed: set = set()

    for key, members in positional.items():
        # Two works both claiming the same position is a contradiction — an
        # author reusing a phrase rather than declaring a sequence.
        positions = [m["position"] for m in members]
        if len(set(positions)) != len(positions):
            continue

        name = members[0]["series_name"]
        members = sorted(members, key=lambda m: m["position"])
        ids = {m["id"] for m in members}

        # Extend with the same author's works that name this series without
        # placing themselves in it. Position None: we know they belong, not
        # where, and inventing an order would be a claim we cannot support.
        for w in works:
            if w["id"] in ids or w["id"] in claimed:
                continue
            if mentions_series(w.get("summary"), name):
                members.append({**w, "series_name": name, "position": None})
                ids.add(w["id"])

        if len(members) < 2:
            continue
        claimed |= ids
        out[name] = members
    return out


# ── Applying it ──────────────────────────────────────────────────────────────

def run(db, dry_run: bool = True, only_author: str | None = None,
        limit_authors: int | None = None) -> tuple[int, int]:
    """Find and store summary-declared series. Returns (series, works placed).

    Only looks at works whose summary could possibly contain a declaration, so
    the scan is a single indexed-ish pass rather than 19.9M rows of regex. Stores
    through the same tables and the same conflict handling as series_detect.py,
    with source='summary' so the two are distinguishable afterwards.
    """
    from collections import defaultdict as _dd
    from sqlalchemy import text as _t

    db.execute(_t("SET statement_timeout = 0"))
    where = ["delisted_at IS NULL", "author IS NOT NULL", "author <> ''",
             "summary ~* :pat"]
    params = {"pat": r"(part|book|story|instal?lment|volume|vol\.?)\s*"
                     r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten"
                     r"|first|second|third|fourth|fifth)\s+(of|in)\s"}
    if only_author:
        where.append("lower(author) = :a")
        params["a"] = only_author.strip().lower()

    rows = db.execute(_t(f"""
        SELECT author, id::text, title, coalesce(summary,''), site
          FROM stories WHERE {' AND '.join(where)}
    """), params).fetchall()

    # The declaring works tell us which authors are worth looking at; the group
    # then needs that author's OTHER works too, because the second member is
    # often the one that only mentions the series.
    authors = sorted({r[0] for r in rows})
    if limit_authors:
        authors = authors[:limit_authors]

    series = placed = 0
    for i, author in enumerate(authors, 1):
        works = [{"id": r[0], "title": r[1], "summary": r[2], "site": r[3]}
                 for r in db.execute(_t("""
                     SELECT id::text, title, coalesce(summary,''), site
                       FROM stories
                      WHERE lower(author) = lower(:a) AND delisted_at IS NULL
                      LIMIT 400
                 """), {"a": author}).fetchall()]
        groups = group_by_declaration(works)
        for name, members in groups.items():
            series += 1
            placed += len(members)
            if dry_run:
                print(f"  {author} — {name}")
                for m in members:
                    print(f"      {m['position'] or '-'}. {m['title'][:60]}")
                continue
            sid = db.execute(_t("""
                INSERT INTO series (name, author, site, source, confidence, work_count)
                VALUES (:n, :a, :s, 'summary', :c, :w)
                ON CONFLICT (lower(coalesce(author,'')), lower(name)) DO UPDATE
                    SET work_count = EXCLUDED.work_count,
                        confidence = EXCLUDED.confidence
                RETURNING id
            """), {"n": name, "a": author, "s": members[0]["site"],
                   # An explicit positional declaration by the author is the
                   # strongest evidence available short of an archive field.
                   "c": 0.9, "w": len(members)}).scalar()
            for m in members:
                db.execute(_t("""
                    INSERT INTO series_works (series_id, story_id, position, role)
                    VALUES (:s, :w, :p, 'main')
                    ON CONFLICT (series_id, story_id) DO UPDATE
                        SET position = EXCLUDED.position
                """), {"s": sid, "w": m["id"], "p": m["position"]})
                db.execute(_t("UPDATE stories SET has_series = true "
                              "WHERE id = :w AND NOT has_series"), {"w": m["id"]})
        if not dry_run and i % 200 == 0:
            db.commit()
    if not dry_run:
        db.commit()
    return series, placed


if __name__ == "__main__":
    import argparse
    from db.session import SessionLocal

    ap = argparse.ArgumentParser(description="Series declared in summaries.")
    ap.add_argument("--apply", action="store_true", help="Write them (default: dry run).")
    ap.add_argument("--author", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    with SessionLocal() as s:
        n, w = run(s, dry_run=not a.apply, only_author=a.author, limit_authors=a.limit)
    print(f"{'stored' if a.apply else 'would store'}: {n:,} series, {w:,} works")
