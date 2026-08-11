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
    declaration), `summary` and `published_at`. Returns the same dicts, sorted,
    each with `position` filled in and `position_source` recording which signal
    decided it — so the UI can say how confident the order is, and so a wrong
    order is diagnosable rather than mysterious.

    Explicit positions are never overwritten. A canon anchor fills a gap. Date
    breaks the remaining ties and nothing more.
    """
    scored = []
    for m in members:
        explicit = m.get("position")
        if explicit is not None:
            key, source = (0, float(explicit)), "declared"
        else:
            canon = canon_position(m.get("summary"))
            if canon is not None:
                key, source = (0, float(canon)), "canon"
            else:
                # No positional signal at all: after everything that has one,
                # ordered among themselves by date.
                ts = m.get("published_at")
                key = (1, ts.timestamp() if hasattr(ts, "timestamp") else 0.0)
                source = "date" if ts else "unknown"
        scored.append((key, source, m))

    scored.sort(key=lambda x: (x[0], str(x[2].get("id"))))
    out = []
    for n, (_key, source, m) in enumerate(scored, 1):
        out.append({**m, "position": n, "position_source": source})
    return out
