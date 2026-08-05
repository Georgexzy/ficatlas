r"""
Find series the way titles actually announce them: a stem plus a number.
========================================================================

The first algorithm grouped an author's works by a rare shared word. Sampling
fourteen real results, four were series and ten were not:

    Arthurs Awakening PART 1 / PART 2 / PART 3 / PART 4     a series
    Blood Chapters 1-3 / 4-6 / 7-9 / 13-15                  a series
    Ghostober Day 2 / Day 5 / Day 6 / Day 23                a series
    DC-SlashCon 2021 / 2022 / 2023 Opening Vid              a series

    Aang's Scary Dream / Aang Bond / Aang and his friend    a character
    Roomates (Upstead) / History. Upstead / Captured-Upstead  a ship
    Wet and Messy / Mari Gets Messy / A Trifle Messy        a kink tag
    Video: Love at War / Video Everything I Am              a format label

A rare shared word cannot tell those apart, because both groups have one. What
separates them is visible at a glance: the real series carry a STEM AND A
NUMBER, and the false ones share a word scattered anywhere in the sentence.

That matches how the problem is solved outside fandom. US 9,244,919 (organizing
books by series) clusters by author and then matches on common title strings
together with a book number — the number is not decoration, it is the evidence
that an order exists.

So membership requires both:

    a common STEM   the titles agree on a leading run of words, and
    a distinct NUMBER  each strips to the same stem via a different position

"Aang's Scary Dream" and "Aang Bond" share a word and neither strips to
anything, so they are not a series, and this says so.
"""

import re

# Every way a position gets written into a fanfic title. Ordered so the more
# specific patterns win: "Chapters 1-3" must be tried before a bare "1".
_MARKERS = [
    # Chapters 1-3, Chapter 4-6, Ch. 9
    (re.compile(r"[\s\-–—:,(\[]*\bch(?:apter)?s?\.?\s*(\d{1,4})\s*(?:[-–—]\s*\d{1,4})?\s*\)?\]?\s*$", re.I), "chapter"),
    # Part 1, Part IV, pt 2
    (re.compile(r"[\s\-–—:,(\[]*\bp(?:ar)?t\.?\s*(\d{1,3}|[ivxlc]{1,6})\b\s*\)?\]?\s*$", re.I), "part"),
    # Book 2, Volume III, Vol. 4
    (re.compile(r"[\s\-–—:,(\[]*\b(?:book|vol(?:ume)?)\.?\s*(\d{1,3}|[ivxlc]{1,6})\b\s*\)?\]?\s*$", re.I), "book"),
    # Day 5, Week 2, Round 3 — prompt-challenge series, very common
    (re.compile(r"[\s\-–—:,(\[]*\b(?:day|week|round|prompt|entry)\s*(\d{1,3})\b\s*\)?\]?\s*$", re.I), "day"),
    # (1/5), 2/7
    (re.compile(r"[\s\-–—:,(\[]*\(?\s*(\d{1,3})\s*/\s*\d{1,3}\s*\)?\s*$"), "fraction"),
    # #3
    (re.compile(r"[\s\-–—:,(\[]*#\s*(\d{1,3})\s*$"), "hash"),
    # A trailing bare number or roman numeral: "Insomnia III", "Nightfall 2"
    (re.compile(r"\s+(\d{1,3}|[ivxlc]{2,6})\s*$", re.I), "bare"),
]

# How much of a title the shared stem must account for, when there is no number
# to prove an order. See the note where it is used.
STEM_SHARE = 0.45

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13,
          "xiv": 14, "xv": 15, "xx": 20}


def _as_int(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        n = int(raw)
        return n if 0 < n < 500 else None
    return _ROMAN.get(raw)


# A leading bracket tag: 【TSN/ME】, [Podfic], (Translation). These mark the
# fandom, the format or the language, and every work by that author carries the
# same one — so comparing titles with them attached groups an author's whole
# output under a label that says nothing about sequence.
_LEADING_TAG = re.compile(r"^\s*[\[\(【〈《][^\]\)】〉》]{1,40}[\]\)】〉》]\s*")


def normalise(text: str) -> str:
    """Comparable form of a title: lowercase, punctuation flattened."""
    t = _LEADING_TAG.sub("", text or "")
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def split_position(title: str) -> tuple[str, int | None, str | None]:
    """(stem, position, marker kind). Position is None when the title has none.

    A YEAR is deliberately accepted as a position — "DC-SlashCon 2021 / 2022 /
    2023" is a real annual sequence, and treating four digits as ordinary text
    lost it. Handled before the generic patterns because 2021 would otherwise
    read as a bare number far outside any plausible part count.
    """
    t = (title or "").strip()
    if not t:
        return "", None, None

    year = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    if year:
        stem = (t[:year.start()] + " " + t[year.end():]).strip(" -–—:,()[]")
        if len(normalise(stem)) >= 3:
            return normalise(stem), int(year.group(1)), "year"

    for rx, kind in _MARKERS:
        m = rx.search(t)
        if not m:
            continue
        pos = _as_int(m.group(1))
        if pos is None:
            continue
        stem = t[:m.start()].strip(" -–—:,()[]")
        if len(normalise(stem)) < 3:
            continue          # "Part 2" alone is not a series stem
        return normalise(stem), pos, kind

    return normalise(t), None, None


def common_stem(a: str, b: str, min_words: int = 2) -> str | None:
    """The leading words two titles agree on, if that is enough to mean anything.

    Used for series whose parts are distinguished by a subtitle rather than a
    number — "The Bodyguard: Arrival" / "The Bodyguard: Departure". Requires
    whole words, not characters: a character prefix matches "The Bo" across two
    unrelated works, and requiring two words means a shared "The" cannot carry
    a group on its own.
    """
    wa, wb = normalise(a).split(), normalise(b).split()
    n = 0
    while n < min(len(wa), len(wb)) and wa[n] == wb[n]:
        n += 1
    if n < min_words:
        return None
    stem = " ".join(wa[:n])
    # A stem that IS one of the titles entire tells us nothing about the other.
    if len(stem) < 6:
        return None
    return stem


def group_by_structure(works: list[dict]) -> list[dict]:
    """Series among one author's works, evidenced by structure rather than by a
    shared word. Returns [{stem, members:[(work, position)], kind}].

    Two shapes are accepted:

      numbered   several titles strip to the SAME stem via different positions.
                 The strongest evidence there is: the author wrote the order in.
      stemmed    several titles share a leading run of at least two words and
                 six characters, and no two of them are the same title.

    Everything else is not a series as far as this is concerned, which is the
    change: a shared rare word no longer qualifies on its own.
    """
    numbered: dict[str, list[tuple[dict, int, str]]] = {}
    for w in works:
        stem, pos, kind = split_position(w.get("title") or "")
        if pos is not None and stem:
            numbered.setdefault(stem, []).append((w, pos, kind))

    out: list[dict] = []
    claimed: set[str] = set()

    for stem, entries in numbered.items():
        # Distinct positions only: three works all called "Part 1" are three
        # drafts, not three instalments.
        seen_pos: dict[int, tuple] = {}
        for w, pos, kind in entries:
            seen_pos.setdefault(pos, (w, pos, kind))
        members = list(seen_pos.values())
        if len(members) < 2:
            continue
        members.sort(key=lambda e: e[1])
        out.append({
            "stem": stem,
            "kind": members[0][2],
            "members": [(w, pos) for w, pos, _ in members],
        })
        claimed.update(w["id"] for w, _, _ in members)

    # Stem-only groups, over whatever the numbered pass did not take.
    rest = [w for w in works if w["id"] not in claimed]
    by_stem: dict[str, list[dict]] = {}
    for i, a in enumerate(rest):
        for b in rest[i + 1:]:
            stem = common_stem(a.get("title") or "", b.get("title") or "")
            if not stem:
                continue
            bucket = by_stem.setdefault(stem, [])
            for w in (a, b):
                if all(x["id"] != w["id"] for x in bucket):
                    bucket.append(w)

    for stem, members in sorted(by_stem.items(), key=lambda kv: -len(kv[0])):
        members = [m for m in members if m["id"] not in claimed]
        if len(members) < 3:
            continue          # two works sharing two words is a coincidence
        titles = {normalise(m.get("title") or "") for m in members}
        if len(titles) < len(members):
            continue          # duplicates, not instalments

        # The shared part has to be MOST of the title, not a fragment at the
        # front of it. Without this, two-word prefixes swept up whole shelves:
        # "Fang Hua" is a name, and it grouped 22 unrelated works because every
        # title happened to start with it.
        #
        #     Places Left (to find)      stem is 55% of the title   a series
        #     apocalypse of lust The     82%                        a series
        #     Fang Hua Xiang Qing Yuan   33%                        a name
        #
        # Measured on the transliterated titles where this failed worst, since
        # those split into many short words and make a prefix cheap to share.
        avg = sum(len(t) for t in titles) / len(titles)
        if avg <= 0 or len(stem) / avg < STEM_SHARE:
            continue
        claimed.update(m["id"] for m in members)
        out.append({"stem": stem, "kind": "stem",
                    "members": [(m, None) for m in members]})

    return out


# Words a series name should not END on. A stem is cut wherever the titles stop
# agreeing, which lands mid-phrase as often as not — real output included "Han
# JiSung y la", "His Majesty's Three Nights: In" and "The Sickness Returns
# Chap". The group was right in every case; only the label was wrong.
_TRAILING_JUNK = {
    # structural words left behind when the number was stripped
    "chap", "chapter", "chapters", "ch", "part", "pt", "book", "vol", "volume",
    "day", "week", "round", "prompt", "entry", "no", "number",
    # function words in several languages, which never end a title
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "is", "was", "y", "la", "el", "los", "las", "de", "del", "un", "una",
    "le", "les", "du", "des", "et", "der", "die", "das", "und", "e", "il",
}


def tidy_name(name: str) -> str:
    """Trim a stem back to somewhere a name can plausibly end."""
    # An unclosed bracket: "Estranged and All Alone (Act" — the stem was cut
    # inside a parenthetical, and half a bracket reads as a typo.
    name = (name or "").strip()
    for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
        if name.count(open_ch) > name.count(close_ch):
            name = name[:name.rindex(open_ch)].strip()
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    while parts:
        last = re.sub(r"[^\w]", "", parts[-1]).lower()
        if last in _TRAILING_JUNK or (last.isdigit() and len(parts) > 1):
            parts.pop()
            continue
        break
    out = " ".join(parts).strip(" -–—:,.;_")
    return out
