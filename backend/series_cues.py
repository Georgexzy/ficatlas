r"""
Read what the author said about the series, not just what the titles rhyme with.
================================================================================

series_detect.py groups by distinctive shared title words. That finds the
Dangerverse, and it cannot find anything an author signalled in prose — which,
on FanFiction.net, is where nearly all of it lives, because FFN has no series
field to put it in. Real summaries from the index:

    "Sequel to That's Who She Is."
    "Sequel to Jackson Junior: I'm Right Here, and prequel to Jackson Junior."
    "A sequel to Memento"
    "Third in the Facing the Future series"
    "Book 2 of the Chronicles of..."

Two quite different kinds of statement, and they are worth separating:

  NAMED SERIES   "third in the Facing the Future series" gives a name AND a
                 position. Strongest signal available short of AO3's own field:
                 nothing has to be guessed, only parsed.

  RELATIVE LINK  "Sequel to Memento" names another WORK, not a series. It
                 establishes an edge between two stories rather than membership
                 of a set, so the set has to be assembled by following edges —
                 A is a sequel to B, C is a sequel to A — and then named after
                 whichever work has nothing before it.

The second is where care is needed. "Sequel to Memento" is only useful if we can
resolve "Memento" to a work, and the obvious resolution — search every title in
the index — is wrong: thousands of works are called Memento. It is resolved
against THE SAME AUTHOR'S other works only, which is nearly always what the
phrase means, and produces nothing rather than a wrong link when it does not
match. An author telling you their story follows another of theirs is a fact; a
title collision across 19.7M works is a coincidence.
"""

import logging
import re

log = logging.getLogger(__name__)

# "third in the Facing the Future series", "Book 2 of the Chronicles", "part IV
# of the Dangerverse". Captures position and name together.
ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
          "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}

_ORD_WORDS = "|".join(ORDINALS)

# "Third in the X series" / "3rd story in the X trilogy"
_NAMED_ORDINAL = re.compile(
    rf"\b({_ORD_WORDS})\b(?:\s+\w+){{0,2}}?\s+(?:in|of)\s+(?:the\s+)?"
    r"[\"'“]?(.{2,60}?)[\"'”]?\s*(series|trilogy|verse|saga|cycle|arc)\b", re.I)

# "Part 3 of the X series" / "Book II of X"
_NAMED_NUMBER = re.compile(
    r"\b(?:part|book|story|installment|instalment|volume)\s+(\d{1,2}|[ivx]{1,5})\b"
    r"\s*(?:of|in)\s+(?:the\s+)?[\"'“]?(.{2,60}?)[\"'”]?"
    r"\s*(series|trilogy|verse|saga|cycle|arc)?\s*(?:[.,;!]|$)", re.I)

# "the Dangerverse", "the Wastelands series" — a name with no position.
_NAMED_BARE = re.compile(
    r"\b(?:of|in|part of)\s+(?:the\s+|my\s+)?[\"'“]?(.{2,50}?)[\"'”]?\s*"
    r"(series|trilogy|verse|saga|universe|continuity)\b", re.I)

# "Sequel to X", "prequel to X", "side story to X", "continues from X".
#
# Which phrases to support was measured rather than guessed, over a 400,000-work
# sample of our own summaries — no published algorithm for this exists, so the
# corpus is the only honest authority:
#
#     sequel / prequel to        2,256
#     read X first / before        370
#     in the X universe/'verse     306
#     side story / companion to    113
#     continues / follows on from   19
#     "Book Two" etc IN THE TITLE     0   <- not implemented, it does not happen
#
# The last line is why there is no title-numbering rule: it looked like an
# obvious signal and the data says authors simply do not write titles that way.
_RELATIVE = re.compile(
    r"\b(sequel|prequel|follow[- ]?up|companion(?:\s+piece)?|side[- ]?story|continuation)"
    r"\s+to\s+[\"'“]?(.{2,70}?)[\"'”]?\s*(?:[.,;!]|$|\band\b)", re.I)

# "continues from X", "follows on from X" — same edge, different phrasing.
_CONTINUES = re.compile(
    r"\b(?:continues|follows\s+on|picks\s+up)\s+(?:directly\s+)?from\s+"
    r"[\"'“]?(.{2,70}?)[\"'”]?\s*(?:[.,;!]|$)", re.I)

# "read X first", "you should read X before this". States an ORDER directly,
# which is worth more than mere membership: it says which one comes earlier.
_READ_FIRST = re.compile(
    r"\b(?:read|reading)\s+[\"'“]?(.{2,60}?)[\"'”]?\s+"
    r"(?:first|before\s+(?:this|reading))", re.I)

# Phrases that look like a series name but are not one.
_JUNK_NAMES = {
    "this", "that", "my", "his", "her", "their", "a", "an", "the", "same",
    "new", "other", "first", "second", "third", "next", "last", "above", "it",
}


def _clean_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", (raw or "")).strip(" \"'“”‘’.,:;-–—")
    # Strip a leading article, and the qualifiers people put in front of a
    # continuity's name when distinguishing it from its own side stories.
    # "the main Dangerverse" and "the Dangerverse" are one series, and splitting
    # them produced two entries for the same five books.
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I).strip()
    name = re.sub(r"^(?:main|original|core|proper)\s+", "", name, flags=re.I).strip()
    if len(name) < 3 or len(name) > 45:
        return None
    # A series name is a name, not a clause. These came out of real summaries:
    # "and out of time, like the Fates spinning the universe" was recorded as a
    # series because the sentence happened to contain "in the ... universe".
    # A name does not begin with a conjunction, and it does not contain a comma
    # or a run of small words.
    if re.match(r"^(and|but|or|so|then|which|that|who|when|where|while)\b", name, re.I):
        return None
    if "," in name or ";" in name:
        return None
    if len(name.split()) > 6:
        return None
    if name.lower() in _JUNK_NAMES:
        return None
    # A "name" that is only punctuation or digits is a parse artefact.
    if not re.search(r"[A-Za-z]{3}", name):
        return None
    return name


def _as_int(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _ROMAN.get(raw) or ORDINALS.get(raw)


def parse_named(summary: str | None) -> dict | None:
    """A series the author NAMED, with a position where they gave one."""
    if not summary:
        return None
    for rx, pos_group, name_group in (
        (_NAMED_ORDINAL, 1, 2),
        (_NAMED_NUMBER, 1, 2),
        (_NAMED_BARE, None, 1),
    ):
        m = rx.search(summary)
        if not m:
            continue
        name = _clean_name(m.group(name_group))
        if not name:
            continue
        pos = _as_int(m.group(pos_group)) if pos_group else None
        kind = None
        # Keep "verse"/"trilogy" in the name when the author used it, since that
        # is how readers refer to it — "the Dangerverse", not "the Danger series".
        for g in m.groups():
            if g and g.lower() in ("verse", "trilogy", "saga", "cycle", "arc",
                                   "universe", "continuity"):
                kind = g.lower()
        display = name if kind and name.lower().endswith(kind) else (
            f"{name}{kind}" if kind == "verse" else
            f"{name} {kind}" if kind else f"{name} series")
        return {"name": display, "position": pos}
    return None


def parse_relative(summary: str | None) -> list[dict]:
    """Works this one says it follows: [{"kind": "sequel", "title": "..."}]."""
    if not summary:
        return []
    out = []
    for m in _RELATIVE.finditer(summary):
        title = _clean_name(m.group(2))
        if title:
            out.append({"kind": m.group(1).lower().replace(" ", "-"),
                        "title": title})
    for rx, kind in ((_CONTINUES, "continues-from"), (_READ_FIRST, "read-first")):
        for m in rx.finditer(summary):
            title = _clean_name(m.group(1))
            # "read this first" and "read it first" name no work at all.
            if title and title.lower() not in _JUNK_NAMES:
                out.append({"kind": kind, "title": title})
    return out


def link_by_relatives(works: list[dict]) -> list[list[dict]]:
    """Assemble chains from "sequel to" statements, within one author's works.

    Resolved against the same author only. "Sequel to Memento" is a fact about
    that author's own shelf; matching it against all 19.7M titles would find
    thousands of unrelated works called Memento and assert a sequence between
    strangers.
    """
    # Both sides normalised the same way. _clean_name strips a leading article
    # from the cue ("Side story to The Long Road" -> "Long Road"), so matching
    # against raw titles would miss every work whose title starts with "The" —
    # which is a great many of them.
    def norm(t: str) -> str:
        t = re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())
        t = re.sub(r"^(?:the|a|an)\s+", "", t).strip()
        return re.sub(r"\s+", " ", t)

    by_title = {}
    for w in works:
        key = norm(w.get("title") or "")
        if key:
            by_title.setdefault(key, w)

    # union-find over "follows" edges
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edges = 0
    for w in works:
        for rel in parse_relative(w.get("summary")):
            other = by_title.get(norm(rel["title"]))
            if other and other["id"] != w["id"]:
                union(w["id"], other["id"])
                edges += 1
    if not edges:
        return []

    groups: dict[str, list[dict]] = {}
    for w in works:
        if w["id"] in parent:
            groups.setdefault(find(w["id"]), []).append(w)
    return [g for g in groups.values() if len(g) >= 2]


# A bare "-verse" or "-'verse" name mentioned anywhere in a summary.
#
# Fans name a continuity this way constantly and it is the name they use for it,
# so it beats anything we could construct. whydoyouneedtoknow's five books share
# the word "danger" in their titles, which is what the title matcher finds — but
# their summaries say "Dangerverse", which is what the series is actually
# called. Naming it "Danger series" would have been our invention sitting where
# the author's own word was available.
_VERSE_WORD = re.compile(r"\b([A-Z][A-Za-z]{2,24})['’]?verse\b")


def stated_name(summaries) -> str | None:
    """The name the AUTHOR uses for this series, from any of its works.

    Tried against every member before falling back to a constructed name, and
    the most frequently repeated wins — one work mentioning another author's
    "Potterverse" in passing should not outvote four saying "Dangerverse".
    """
    from collections import Counter
    votes: Counter = Counter()
    for text in summaries:
        if not text:
            continue
        cue = parse_named(text)
        if cue and cue.get("name"):
            votes[cue["name"]] += 2      # an explicit "Nth in the X series"
        for m in _VERSE_WORD.finditer(text):
            stem = m.group(1)
            # "main Dangerverse" is the Dangerverse.
            if stem.lower() in ("main", "original", "core"):
                continue
            if stem.lower() in ("uni", "multi", "meta", "omni", "cross"):
                continue                 # "universe", "multiverse", "metaverse"
            votes[f"{stem}verse"] += 1
    if not votes:
        return None
    best, n = votes.most_common(1)[0]
    # One passing mention is not a name. Two independent works calling it the
    # same thing is.
    return best if n >= 2 else None
