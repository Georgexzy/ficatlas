"""Working out what order a series goes in.

Membership and order are separate problems. series_from_summary.py and
series_detect.py answer "do these belong together?"; this answers "which comes
first?", for the members those detectors could not place.

The Sacrifices Arc is the case that forced it. Seven works, and only one of them
states a position:

    Saving Connor                        AU, Slytherin!Harry, Harry's twin…
    No Mouth But Some Serpent's          AU of CoS
    Freedom And Not Peace                AU of GoF
    Wind That Shakes the Seas and Stars  AU of OoTP
    A Song In Time of Revolution         AU of HBP
    I Am Also Thy Brother                AU, part 7 of Sacrifices
    Maze of Light                        short story set in my Sacrifices universe

The order is right there and is not in the titles: each work names the canon
book it diverges from. That is a strong signal in book fandoms, where the source
material is already numbered, and it is the reason a title matcher and a
sequel-chain matcher both come back empty here.

Signals, strongest first
------------------------
1. An explicit declaration — "part 7 of Sacrifices". The author said it.
2. A canon anchor — "AU of GoF" means the fourth book, so it sits fourth.
3. Publication date. Deliberately last and never on its own: it is wrong often
   enough to matter. Authors repost older work, backdate, publish a prequel
   years after the book it precedes, and bulk imports carry the import date
   rather than the original. As a tiebreaker between works that have no better
   signal it is useful; as the primary ordering it would confidently invent
   sequences that are simply wrong.
"""
from __future__ import annotations

import re

# Canon book positions for the fandoms where this actually pays.
#
# Harry Potter alone is 1.14M works here, and its abbreviations are near
# universal in summaries — an author writing "AU of OoTP" is telling you the
# work sits fifth without meaning to. Only unambiguous abbreviations are listed:
# "HP" means the fandom rather than a book, and is deliberately absent.
_CANON_BOOKS: dict[str, int] = {
    # Harry Potter
    "ps": 1, "sorcerer's stone": 1, "philosopher's stone": 1, "sorcerers stone": 1,
    "philosophers stone": 1, "ss": 1,
    "cos": 2, "chamber of secrets": 2,
    "poa": 3, "prisoner of azkaban": 3,
    "gof": 4, "goblet of fire": 4,
    "ootp": 5, "otp": None, "order of the phoenix": 5,
    "hbp": 6, "half blood prince": 6, "half-blood prince": 6,
    "dh": 7, "deathly hallows": 7,
}
# "OTP" is a ship abbreviation, not Order of the Phoenix. Mapped to None above so
# the intent is visible, and dropped here so it can never match.
_CANON_BOOKS = {k: v for k, v in _CANON_BOOKS.items() if v is not None}

# The phrasings that mean "this work diverges from that book", rather than
# merely mentioning it. "AU of GoF" places the work; "she reads Goblet of Fire on
# the train" does not.
#
# Two steps rather than one clever regex: find a divergence phrase, then look for
# a canon title in the short window after it. A single pattern trying to do both
# had the title group swallow the connecting words ("of CoS" instead of "CoS"),
# which quietly matched nothing at all.
_DIVERGE = re.compile(
    r"\b(?:au|alternate universe|canon[\s-]?divergen\w*|divergence|rewrite"
    r"|retelling|set (?:in|during|after)|takes place (?:in|during|after)"
    r"|starts? (?:in|during|after)|begins? (?:in|during|after)"
    r"|post|pre|during|after|through(?:out)?)\b[\s,:-]*(?:of\s+)?",
    re.IGNORECASE)

# Longest first, so "half blood prince" wins over any shorter token inside it.
_CANON_TOKENS = sorted(_CANON_BOOKS, key=len, reverse=True)


def canon_position(summary: str | None) -> int | None:
    """Which book of the source canon this work diverges from, if it says.

    Returns 1-7 for Harry Potter's books, or None. A divergence phrase is
    required as well as the title, so a summary that only mentions a book places
    nothing.
    """
    if not summary:
        return None
    for m in _DIVERGE.finditer(summary):
        # The title follows the phrase closely when it is being used to place
        # the work; anything further off is a different sentence's business.
        window = summary[m.end():m.end() + 26]
        for token in _CANON_TOKENS:
            if re.match(r"\b" + re.escape(token) + r"\b", window, re.IGNORECASE):
                return _CANON_BOOKS[token]
    return None


def order_members(members: list[dict]) -> list[dict]:
    """Assign a reading order, using the best signal each member has.

    `members` are dicts with `id`, and optionally `position` (an explicit
    declaration), `summary` and `published_at`. Returns them sorted, each with
    `position` filled in and `position_source` recording which signal decided
    it — so the UI can say how confident an order is, and a wrong one is
    diagnosable rather than mysterious.

    Explicit positions are never overwritten; a canon anchor fills a gap; date
    does the rest.

    Date INTERPOLATES rather than trailing
    --------------------------------------
    The first version appended everything unplaced to the end, which put the
    first book of the Sacrifices Arc last. "Saving Connor" opens the series and
    says so nowhere a machine can read — no position, no canon anchor, just the
    premise. What it does have is the earliest publication date of the seven, by
    three weeks.

    So an undated-by-signal member is slotted against the members that DO have a
    signal, by comparing publication dates. That is much safer than ordering by
    date outright: the dated anchors come from the author's own statements, and
    the date is only used to decide where an unplaced work falls BETWEEN them. A
    work published before every anchored one goes first; one published between
    two goes between them.

    It still refuses to guess when it cannot. With no date, or no anchored member
    to compare against, the member keeps its place at the end and is marked
    "unknown" rather than being given a confident position it has not earned.
    """
    def _ts(m):
        d = m.get("published_at")
        return d.timestamp() if hasattr(d, "timestamp") else None

    # Anchored members: the ones a real signal placed.
    anchored = []
    for m in members:
        explicit = m.get("position")
        if explicit is not None:
            anchored.append((float(explicit), "declared", m))
            continue
        canon = canon_position(m.get("summary"))
        if canon is not None:
            anchored.append((float(canon), "canon", m))
    anchored.sort(key=lambda x: x[0])

    # Renumber the anchors 1..n so an unplaced member can be slotted between
    # them on a scale with room in it, whatever the raw canon numbers were.
    anchor_points = [(_ts(m), i + 1.0, m) for i, (_p, _s, m) in enumerate(anchored)]
    dated_anchors = [(ts, rank) for ts, rank, _m in anchor_points if ts is not None]

    placed_ids = {id(m) for _p, _s, m in anchored}
    scored = []
    for rank, (_p, source, m) in zip(range(1, len(anchored) + 1), anchored):
        scored.append((float(rank), source, m))

    for m in members:
        if id(m) in placed_ids:
            continue
        ts = _ts(m)
        if ts is None or not dated_anchors:
            # Nothing to compare, so nothing is claimed: last, and labelled.
            scored.append((float(len(anchored)) + 1000.0, "unknown", m))
            continue
        earlier = [r for t, r in dated_anchors if t <= ts]
        later = [r for t, r in dated_anchors if t > ts]
        if not earlier:
            key = min(later) - 0.5          # before every anchored work
        elif not later:
            key = max(earlier) + 0.5        # after every anchored work
        else:
            key = (max(earlier) + min(later)) / 2.0
        scored.append((key, "date", m))

    scored.sort(key=lambda x: (x[0], str(x[2].get("id"))))
    return [{**m, "position": n, "position_source": source}
            for n, (_k, source, m) in enumerate(scored, 1)]


# Words too ordinary to identify anything. Everything else is judged by how
# rarely the author themselves uses it, which is a better test than any fixed
# list could be.
_COMMON = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "were", "will", "would", "when", "what", "who", "she", "her", "his", "him",
    "they", "them", "their", "but", "not", "all", "out", "into", "back", "after",
    "before", "story", "chapter", "fic", "please", "review", "reviews", "read",
    "first", "last", "one", "two", "new", "old", "more", "just", "like", "about",
    "harry", "potter", "hogwarts", "slash", "fluff", "angst", "canon", "years",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'!-]{2,}")


def premise_tokens(summary: str | None) -> set[str]:
    """Distinctive words from a summary — names, and fandom notation.

    "Slytherin!Harry" survives as one token on purpose: the bang notation is how
    fandom marks a characterisation, and it is far more identifying than either
    half alone.
    """
    if not summary:
        return set()
    out = set()
    for raw in _TOKEN.findall(summary):
        tok = raw.strip("'-").lower()
        if len(tok) < 4 or tok in _COMMON:
            continue
        out.add(tok)
    return out


def shares_premise(candidate: dict, members: list[dict], min_members: int = 2) -> bool:
    """Does this work share a distinctive premise with an established series?

    The last resort for membership, and the one that catches a first book.
    "Saving Connor" opens the Sacrifices Arc while declaring nothing: no
    position, no canon anchor, no "sequel to". What it shares with its own
    sequels is the premise — Slytherin!Harry, and a twin brother called Connor —
    and those words appear in summary after summary because they are what the
    series is about.

    Deliberately demanding. A token has to appear in at least two established
    members, so a word that happens to occur once proves nothing, and the
    ordinary vocabulary of a summary is excluded outright. On its own this would
    be far too loose to build a series from; as a way of adding a work to a
    series that other evidence already established, for one author, it holds.
    """
    cand = premise_tokens(candidate.get("summary"))
    if not cand:
        return False
    counts: dict[str, int] = {}
    for m in members:
        for tok in premise_tokens(m.get("summary")):
            counts[tok] = counts.get(tok, 0) + 1
    return any(counts.get(tok, 0) >= min_members for tok in cand)
