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
    r"\b(?:of|in|part of)\s+(?:the\s+)?[\"'“]?(.{2,50}?)[\"'”]?\s*"
    r"(series|trilogy|verse|saga)\b", re.I)

# "Sequel to X", "prequel to X", "follows X", "companion piece to X"
_RELATIVE = re.compile(
    r"\b(sequel|prequel|follow[- ]?up|companion(?:\s+piece)?|continuation)\s+to\s+"
    r"[\"'“]?(.{2,70}?)[\"'”]?\s*(?:[.,;!]|$|\band\b)", re.I)

# Phrases that look like a series name but are not one.
_JUNK_NAMES = {
    "this", "that", "my", "his", "her", "their", "a", "an", "the", "same",
    "new", "other", "first", "second", "third", "next", "last", "above", "it",
}


def _clean_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", (raw or "")).strip(" \"'“”‘’.,:;-–—")
    # Strip a leading article: "the Dangerverse" and "Dangerverse" are one series.
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I).strip()
    if len(name) < 3 or len(name) > 60:
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
            if g and g.lower() in ("verse", "trilogy", "saga", "cycle", "arc"):
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
    return out


def link_by_relatives(works: list[dict]) -> list[list[dict]]:
    """Assemble chains from "sequel to" statements, within one author's works.

    Resolved against the same author only. "Sequel to Memento" is a fact about
    that author's own shelf; matching it against all 19.7M titles would find
    thousands of unrelated works called Memento and assert a sequence between
    strangers.
    """
    by_title = {}
    for w in works:
        key = (w.get("title") or "").strip().lower()
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
            other = by_title.get(rel["title"].lower())
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
