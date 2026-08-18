"""Ordering a series from what each work says it is the sequel to.

The other two detectors find MEMBERSHIP. This one finds ORDER, which is the part
a reader actually needs — knowing seven works belong together is little help if
you cannot tell which to read first.

The signal
----------
103,302 works in this index say "Sequel to X" in their summary, and about ten
thousand more say "Prequel to X". Unlike a shared title word, that is a directed
statement: it names another work AND says which side of it this one falls on.
Chain those edges and a sequence orders itself.

    "Sequel to Finding Olivia"  on  Saving Elliot     ->  Finding Olivia < Saving Elliot
    "Prequel to The Dark Forest" on An Awkward Night  ->  An Awkward Night < The Dark Forest

Measured before building: of 4,386 declarations across 3,073 authors' complete
catalogues, 73% resolved to another work by the same author — 60% on an exact
title match and 13% more once the captured text was allowed to run past the
title into the rest of the sentence, which is how most summaries are written.

Why the earlier measurement said 5%
-----------------------------------
The first attempt loaded only the works that DECLARE a sequel. The work each one
points at is the earlier story, which usually declares nothing, so it was absent
from the sample by construction and could never be matched. Resolution has to be
done against an author's whole catalogue.

Why chapter author-notes are not the source
-------------------------------------------
They carry the same signal — 1,628 chapters here announce a sequel, often naming
it — but we hold chapter text for only ~30k hosted works, against summaries for
all 19.9M. The notes are a 60x smaller pool covering the wrong subset: hosted
FictionAlley works rather than the FanFiction.net catalogue where series ordering is
missing in the first place.
"""
from __future__ import annotations

import re
from collections import defaultdict

# "Sequel to X", "Prequel to 'X'", "sequel (of sorts) to X".
#
# The title is captured greedily to a sentence boundary because most summaries
# run straight on — "Sequel to Ice cubes and Cheese strings. They team overhears
# Grissom..." — and the resolver trims it back by matching against real titles.
_REF = re.compile(
    r"\b(?P<rel>se|pre)quel\s+(?:\([^)]{0,20}\)\s*)?to\s+"
    r"(?:"
    r"\"(?P<dq>[^\"]{2,80})\""
    # A single-quoted title may itself contain apostrophes: "'Let's Fall in
    # Love'" closed at the one in "Let's" and captured the word "Let". A closing
    # quote only counts where a quote plausibly ends — whitespace, sentence
    # punctuation, or the end of the summary.
    r"|'(?P<sq>[^']{2,80})'(?=[\s.,;!?)\]]|$)"
    r"|(?P<bare>[^.;!?\n]{2,70})"
    r")",
    re.IGNORECASE)

# Phrases that name no work. "sequel to this" is about the work you are reading.
_VAGUE = {"this", "it", "that", "my other story", "my first story", "the first",
          "the above", "the original", "my story", "the story", "this one",
          "this fic", "my fic", "the prequel", "the sequel", "part one",
          "part 1", "book one", "book 1", "chapter one"}


def normalise(title: str | None) -> str:
    """Titles as they compare, not as they are punctuated."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def extract_reference(summary: str | None) -> tuple[str, str] | None:
    """Return (relation, referenced_title_text) or None.

    `relation` is "sequel" or "prequel" and describes THIS work relative to the
    one named: a sequel comes after it, a prequel before.
    """
    if not summary:
        return None
    m = _REF.search(summary)
    if not m:
        return None
    raw = (m.group("dq") or m.group("sq") or m.group("bare") or "").strip("\"'“”‘’ ")
    if not raw or normalise(raw) in _VAGUE:
        return None
    if not re.search(r"[A-Za-z]{2}", raw):
        return None
    return ("sequel" if m.group("rel").lower() == "se" else "prequel"), raw


def resolve(reference: str, titles: dict[str, str], self_id: str) -> str | None:
    """Find which of an author's works a reference points at.

    `titles` maps normalised title -> work id. Exact first, then longest-prefix:
    the captured text usually BEGINS with the real title and then continues into
    the rest of the sentence, so the longest title the reference starts with is
    the right one. Six characters minimum, or short common words match anything.
    """
    ref = normalise(reference)
    if not ref:
        return None
    hit = titles.get(ref)
    if hit and hit != self_id:
        return hit
    for norm_title, wid in sorted(titles.items(), key=lambda kv: -len(kv[0])):
        if len(norm_title) >= 6 and wid != self_id and ref.startswith(norm_title):
            return wid
    return None


def build_edges(works: list[dict]) -> list[tuple[str, str]]:
    """Directed (earlier_id, later_id) pairs from one author's works."""
    titles: dict[str, str] = {}
    for w in works:
        titles.setdefault(normalise(w["title"]), w["id"])

    edges: set[tuple[str, str]] = set()
    for w in works:
        found = extract_reference(w.get("summary"))
        if not found:
            continue
        relation, ref = found
        other = resolve(ref, titles, w["id"])
        if not other:
            continue
        # A sequel comes after what it names; a prequel comes before it.
        edge = (other, w["id"]) if relation == "sequel" else (w["id"], other)
        if edge[0] != edge[1]:
            edges.add(edge)
    return sorted(edges)


def chains(edges: list[tuple[str, str]]) -> list[list[str]]:
    """Link the edges into ordered runs.

    Deliberately simple: this follows unambiguous single-successor links only. An
    author who wrote two different sequels to the same story has branched, and a
    branch is not a reading order — emitting one would be inventing a sequence
    the author did not write. Those are left for the membership detectors, which
    can group without claiming an order.

    Cycles are dropped for the same reason: "A is the sequel to B" and "B is the
    sequel to A" cannot both be true, and picking one is a guess.
    """
    succ: dict[str, set[str]] = defaultdict(set)
    pred: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        succ[a].add(b)
        pred[b].add(a)

    nodes = set(succ) | set(pred)
    # Only follow links that are unambiguous in both directions.
    clean = {a: next(iter(bs)) for a, bs in succ.items()
             if len(bs) == 1 and len(pred[next(iter(bs))]) == 1}

    starts = [n for n in nodes if not pred[n] or len(pred[n]) > 1]
    out: list[list[str]] = []
    seen: set[str] = set()
    for start in sorted(starts):
        if start in seen or start not in clean:
            continue
        run, node = [start], start
        seen.add(start)
        while node in clean:
            node = clean[node]
            if node in seen:          # a cycle; abandon this run
                run = []
                break
            run.append(node)
            seen.add(node)
        if len(run) >= 2:
            out.append(run)
    return out


# ── Applying it ──────────────────────────────────────────────────────────────

def run(db, dry_run: bool = True, only_author: str | None = None) -> tuple[int, int]:
    """Find and store sequel-ordered series. Returns (chains, works placed).

    Named after the first work in the chain, which is how readers refer to these
    ("the Finding Olivia series"), and stored with source='sequel' so it can be
    told apart from the title and summary detectors afterwards.
    """
    from sqlalchemy import text as _t

    db.execute(_t("SET statement_timeout = 0"))
    where = ["delisted_at IS NULL", "author IS NOT NULL", "author <> ''",
             "summary ~* '(se|pre)quel to'"]
    params: dict = {}
    if only_author:
        where.append("lower(author) = :a")
        params["a"] = only_author.strip().lower()
    authors = [r[0] for r in db.execute(_t(
        f"SELECT DISTINCT author FROM stories WHERE {' AND '.join(where)}"), params)]

    n_chains = n_works = 0
    for i, author in enumerate(authors, 1):
        works = [{"id": r[0], "title": r[1], "summary": r[2], "site": r[3]}
                 for r in db.execute(_t("""
                     SELECT id::text, title, coalesce(summary,''), site
                       FROM stories
                      WHERE lower(author) = lower(:a) AND delisted_at IS NULL
                      LIMIT 400
                 """), {"a": author}).fetchall()]
        title_of = {w["id"]: w["title"] for w in works}
        site_of = {w["id"]: w["site"] for w in works}
        for chain in chains(build_edges(works)):
            name = title_of.get(chain[0], "")
            if not name:
                continue
            n_chains += 1
            n_works += len(chain)
            if dry_run:
                print(f"  {author} — {name}")
                for pos, wid in enumerate(chain, 1):
                    print(f"      {pos}. {title_of.get(wid,'')[:60]}")
                continue
            sid = db.execute(_t("""
                INSERT INTO series (name, author, site, source, confidence, work_count)
                VALUES (:n, :a, :s, 'sequel', 0.95, :w)
                ON CONFLICT (lower(coalesce(author,'')), lower(name)) DO UPDATE
                    SET work_count = EXCLUDED.work_count,
                        confidence = EXCLUDED.confidence
                RETURNING id
            """), {"n": name, "a": author, "s": site_of.get(chain[0]),
                   "w": len(chain)}).scalar()
            for pos, wid in enumerate(chain, 1):
                db.execute(_t("""
                    INSERT INTO series_works (series_id, story_id, position, role)
                    VALUES (:s, :w, :p, 'main')
                    ON CONFLICT (series_id, story_id) DO UPDATE
                        SET position = EXCLUDED.position
                """), {"s": sid, "w": wid, "p": pos})
                db.execute(_t("UPDATE stories SET has_series = true "
                              "WHERE id = :w AND NOT has_series"), {"w": wid})
        if not dry_run and i % 200 == 0:
            db.commit()
    if not dry_run:
        db.commit()
    return n_chains, n_works


if __name__ == "__main__":
    import argparse
    from db.session import SessionLocal

    ap = argparse.ArgumentParser(description="Series ordered by sequel declarations.")
    ap.add_argument("--apply", action="store_true", help="Write them (default: dry run).")
    ap.add_argument("--author", default=None)
    a = ap.parse_args()
    with SessionLocal() as s:
        c, w = run(s, dry_run=not a.apply, only_author=a.author)
    print(f"{'stored' if a.apply else 'would store'}: {c:,} chains, {w:,} works")
