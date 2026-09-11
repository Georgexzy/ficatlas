"""
FicAtlas Search Query Parser

Supports natural language + operator syntax in any order:

  harry potter dramione slow burn completed >100k

Operators (case-insensitive, any order):
  fandom:Harry Potter          fandom:"My Hero Academia"
  ship:Draco/Hermione          pairing:Draco/Hermione  rel:Draco/Hermione
  char:Harry                   character:Harry
  tag:"slow burn"              t:angst
  rating:M                     rating:teen  rating:explicit
  status:complete              status:wip   complete  wip  completed
  words:>100k  words:<50k      words:100k-200k
  updated:2024  updated:6m     updated:1y
  lang:English                 language:fr
  -tag:fluff                   -fandom:twilight   (exclude prefix)
  crossover:only               crossover:no
  site:ao3                     site:ffnet   site:fictionalley
                               (also fanfiction.net, ff.net, archiveofourown.org,
                                ffn, ficalley — see SITE_ALIASES)
  series:true                  series:false   (in a series / standalone)

Shorthand without operator key:
  >100k  <50k  100k+           → word count
  complete / completed / wip   → status
  explicit / mature / teen / general → rating
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Token patterns ────────────────────────────────────────────────────────────

# Quoted or unquoted value after a key
_QUOTED  = r'"([^"]+)"'
_BARE    = r'(\S+)'
_VAL     = f'(?:{_QUOTED}|{_BARE})'

# Operator KEYS only. The value is taken separately, because a bare value can be
# several words: `fandom: Harry Potter` is documented as the primary syntax.
#
# The old pattern was `(-?)(\w+):(?:"..."|(\S+))`, which required a non-space
# immediately after the colon and stopped at the first whitespace. So the
# README's own headline example did not parse at all — `fandom: Harry Potter`
# fell through entirely into free text — and `fandom:Harry Potter` captured only
# "Harry", leaving "Potter" as a stray search term.
KEY_RE = re.compile(r'(-?)(\w+)\s*:\s*', re.IGNORECASE)

# A bare multi-word value stops before any of these, so
# `fandom: Harry Potter complete >100k` still yields status and word count
# instead of swallowing them into the fandom name.
_SHORTHAND_RE = re.compile(
    r'^(?:complete|completed|wip|incomplete|ongoing'
    r'|explicit|mature|teen|general|gen'
    r'|[<>]=?[\d.]+[km]?\+?|[\d.]+[km]\+|[\d.]+[km]-[\d.]+[km])$',
    re.IGNORECASE,
)


# Operators whose value is always exactly one word, so free text after them is
# free text rather than part of the value.
#
# A bare value otherwise runs to the next operator key, which is right for the
# things that are genuinely multi-word — `fandom: Harry Potter`, `tag: slow
# burn`, an author's pen name — and wrong for every operator whose values come
# from a fixed list. The result was that a filter and the query could not be
# combined without quoting:
#
#     rating:M harry potter        -> no rating AND no search text. Everything.
#     site:ao3 harry potter        -> no site filter, no text.
#     status:complete harry potter -> status "complete harry potter", no text.
#     updated:2024 harry potter    -> no date, no text.
#
# All four silently returned the whole index or nothing, with no indication that
# the operator had eaten the query. `rating:M harry potter` is an ordinary thing
# to type.
#
# `language` is deliberately NOT here: real values include "Bahasa Indonesia",
# so it keeps the multi-word rule and quoting remains the way to bound it.
_SINGLE_TOKEN = {"sites", "ratings", "status", "word_count", "updated_after",
                 "crossovers", "in_series"}


def _take_value(available: str, canonical: str | None = None) -> tuple[str, int]:
    """Read one operator value from the text following `key:`.

    Returns (value, characters_consumed). A quoted value ends at the closing
    quote; a bare one runs to the end of `available` (already bounded by the next
    operator key), minus any trailing shorthand words, which belong to the query
    rather than to this value — or to exactly one word for the operators in
    _SINGLE_TOKEN.
    """
    if available.startswith('"'):
        end = available.find('"', 1)
        if end == -1:
            return available[1:].strip(), len(available)
        return available[1:end].strip(), end + 1

    words = available.split()

    if canonical in _SINGLE_TOKEN:
        if not words:
            return "", 0
        first = words[0]
        idx = available.index(first)
        return first, idx + len(first)

    keep = len(words)
    # Never strip below one word: `status:complete` and `rating:mature` have a
    # shorthand word as their ENTIRE value, and stripping it discarded the
    # operator altogether.
    while keep > 1 and _SHORTHAND_RE.match(words[keep - 1]):
        keep -= 1
    if keep == 0:
        return "", 0
    value = " ".join(words[:keep])
    # Consume up to the end of the last kept word, so trailing shorthand stays in
    # the free text where the shorthand pass can find it.
    idx, consumed = 0, 0
    for w in words[:keep]:
        idx = available.index(w, idx)
        consumed = idx + len(w)
        idx = consumed
    return value, consumed

# Standalone word-count shorthands:  >100k  <50k  100k+  100k-200k
WORDCOUNT_RE = re.compile(
    r'(?:^|\s)(>|<|>=|<=)?(\d+(?:\.\d+)?)(k|m)(?:\+|-(\d+(?:\.\d+)?)(k|m))?(?=\s|$)',
    re.IGNORECASE,
)

# Status shorthands (standalone words)
STATUS_WORDS = {
    "complete": "complete", "completed": "complete",
    "wip": "in_progress", "incomplete": "in_progress", "ongoing": "in_progress",
}

# Rating shorthands (standalone words)
RATING_WORDS = {
    "explicit": "E", "mature": "M", "teen": "T", "general": "G",
    "not-rated": "NR", "unrated": "NR",
}

# Rating aliases for operator values
RATING_ALIASES = {
    "e": "E", "explicit": "E", "x": "E",
    "m": "M", "mature": "M",
    "t": "T", "teen": "T",
    "g": "G", "general": "G", "all": "G",
    "nr": "NR", "none": "NR", "unrated": "NR",
}

# Archive names → the three values actually stored in stories.site.
#
# The column holds `ao3`, `ffnet` and `fictionalley`, and the parser previously
# just lowercased whatever was typed and passed it through. So `site:ao3` worked
# and every other way of naming the same archive silently matched nothing:
# `site:AO3` was fine by luck, `site:FF.net`, `site:fanfiction.net` and
# `site:archiveofourown.org` all produced a filter no row could satisfy. That
# fails in the worst direction — zero results reads as "the index has none of
# this", not as "that filter was not understood".
#
# Includes the domain forms because they are what someone copies out of a URL
# bar, and `a03` because the digit-zero misreading of AO3 is genuinely common.
SITE_ALIASES = {
    "ao3": "ao3", "a03": "ao3", "archive": "ao3",
    "archiveofourown": "ao3", "archiveofourown.org": "ao3",
    "archive of our own": "ao3", "otw": "ao3",

    "ffnet": "ffnet", "ffn": "ffnet", "ff": "ffnet",
    "ff.net": "ffnet", "fanfiction": "ffnet", "fanfiction.net": "ffnet",
    "fanfictionnet": "ffnet", "www.fanfiction.net": "ffnet",

    "fictionalley": "fictionalley", "ficalley": "fictionalley",
    "fiction alley": "fictionalley", "fa": "fictionalley",
    "fictionalley.org": "fictionalley",
}


def canonical_site(value: str) -> Optional[str]:
    """The stored site code for however an archive was named, or None.

    None rather than a passthrough: an unrecognised archive name is a typo or a
    site this index does not carry, and in both cases dropping the filter and
    searching everything is friendlier than returning nothing at all.
    """
    return SITE_ALIASES.get(" ".join(value.lower().split()))


# Field aliases → canonical name
# A word that is only an operator and a colon: `fandom:`, `-tag:`. Paired with
# a FIELD_ALIASES lookup at the call site — see the note there.
_BARE_OPERATOR_RE = re.compile(r"^-?(\w+):$")

FIELD_ALIASES = {
    "fandom": "fandoms", "fandoms": "fandoms", "f": "fandoms",
    "ship": "relationships", "pairing": "relationships", "rel": "relationships",
    "relationship": "relationships", "relationships": "relationships",
    "char": "characters", "character": "characters", "characters": "characters",
    "tag": "tags", "tags": "tags", "t": "tags",
    "rating": "ratings", "ratings": "ratings", "r": "ratings",
    "status": "status", "s": "status",
    "word": "word_count", "words": "word_count", "wc": "word_count", "w": "word_count",
    "lang": "language", "language": "language",
    "updated": "updated_after", "update": "updated_after", "since": "updated_after",
    "site": "sites",
    # Author was missing entirely, which made `author:` worse than useless: with
    # no alias the whole string fell through to free text, so the literal word
    # "author" became a search term. `author:lightning on the wave` returned
    # four works by other people, and `author:"lightning on the wave"` — the
    # form a careful person types — returned NOTHING, because the stray token
    # had to match too. The ?author= URL parameter has always worked, so the
    # card links were fine and only the search bar lied.
    "author": "author", "by": "author", "writer": "author",
    "crossover": "crossovers", "xover": "crossovers",
    "warn": "warnings", "warning": "warnings",
    "category": "categories", "cat": "categories",
    # Part of a series, or deliberately not. `series:true` / `series:false` is
    # what the search bar writes; `in_series:` is accepted as a synonym because
    # that is the query-parameter name and people mirror what they see in URLs.
    "series": "in_series", "in_series": "in_series",
}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ParsedQuery:
    raw: str = ""
    clean_text: str = ""           # free-text remainder after operators stripped

    # Include
    fandoms: list[str]       = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    characters: list[str]    = field(default_factory=list)
    tags: list[str]          = field(default_factory=list)
    ratings: list[str]       = field(default_factory=list)
    warnings: list[str]      = field(default_factory=list)
    categories: list[str]    = field(default_factory=list)
    sites: list[str]         = field(default_factory=list)

    # Exclude
    exc_fandoms: list[str]       = field(default_factory=list)
    exc_relationships: list[str] = field(default_factory=list)
    exc_characters: list[str]    = field(default_factory=list)
    exc_tags: list[str]          = field(default_factory=list)

    # Scalars
    status: Optional[str]         = None
    language: Optional[str]       = None
    author: Optional[str]         = None
    word_count_min: Optional[int]  = None
    word_count_max: Optional[int]  = None
    updated_after: Optional[str]   = None
    crossovers: Optional[str]      = None  # include | exclude | only
    # True = in a series, False = standalone, None = either.
    in_series: Optional[bool]      = None

    # Meta
    tokens: list[dict] = field(default_factory=list)  # for UI highlighting


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_word_count(val: str) -> tuple[Optional[int], Optional[int]]:
    """Parse >100k / <50k / 100k-200k / 100k+ into (min, max)."""
    val = val.strip().lower()
    # range: 100k-200k
    m = re.match(r'^(\d+(?:\.\d+)?)(k|m)-(\d+(?:\.\d+)?)(k|m)$', val)
    if m:
        def _num(v, u): return int(float(v) * (1000 if u == 'k' else 1_000_000))
        return _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
    # operator: >100k
    m = re.match(r'^(>|<|>=|<=)(\d+(?:\.\d+)?)(k|m)\+?$', val)
    if m:
        op, num, unit = m.groups()
        n = int(float(num) * (1000 if unit == 'k' else 1_000_000))
        if op in ('>', '>='): return n, None
        if op in ('<', '<='): return None, n
    # bare: 100k or 100k+
    m = re.match(r'^(\d+(?:\.\d+)?)(k|m)\+?$', val)
    if m:
        n = int(float(m.group(1)) * (1000 if m.group(2) == 'k' else 1_000_000))
        return n, None
    return None, None


def _parse_date(val: str) -> Optional[str]:
    """Parse relative date like 1y / 6m / 30d / 2024 into ISO date string."""
    from datetime import datetime, timedelta
    val = val.strip().lower()
    m = re.match(r'^(\d+)(d|w|m|y)$', val)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"d": n, "w": n*7, "m": n*30, "y": n*365}[unit]
        return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    # bare year
    if re.match(r'^20\d\d$', val):
        return f"{val}-01-01"
    # full date
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val
    except Exception:
        pass
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_query(raw: str) -> ParsedQuery:
    pq = ParsedQuery(raw=raw)
    text = raw

    # ── 1. Extract operator tokens ──────────────────────────────────────────
    consumed_spans = []

    # Locate every recognised operator key first, so each value can run up to the
    # NEXT key rather than stopping at the first space.
    key_hits = []
    for m in KEY_RE.finditer(raw):
        canonical = FIELD_ALIASES.get(m.group(2).lower())
        if canonical:
            key_hits.append((m.start(), m.end(), m.group(1) == "-", canonical))

    for i, (kstart, kend, exclude_flag, canonical) in enumerate(key_hits):
        next_start = key_hits[i + 1][0] if i + 1 < len(key_hits) else len(raw)
        value, used = _take_value(raw[kend:next_start], canonical)
        # Reject values that are only punctuation ("fandom:::" produced "::").
        if not value or not re.search(r"\w", value):
            continue
        span_end = kend + used

        consumed_spans.append((kstart, span_end))

        token = {"key": canonical, "value": value, "exclude": exclude_flag,
                 "raw": raw[kstart:span_end].strip()}

        if canonical == "fandoms":
            (pq.exc_fandoms if exclude_flag else pq.fandoms).append(value)

        elif canonical == "relationships":
            (pq.exc_relationships if exclude_flag else pq.relationships).append(value)

        elif canonical == "characters":
            (pq.exc_characters if exclude_flag else pq.characters).append(value)

        elif canonical == "tags":
            (pq.exc_tags if exclude_flag else pq.tags).append(value)

        elif canonical == "warnings":
            pq.warnings.append(value)

        elif canonical == "categories":
            pq.categories.append(value)

        elif canonical == "ratings":
            mapped = RATING_ALIASES.get(value.lower())
            if mapped:
                pq.ratings.append(mapped)
                token["value"] = mapped

        elif canonical == "status":
            pq.status = STATUS_WORDS.get(value.lower(), value.lower())
            token["value"] = pq.status

        elif canonical == "word_count":
            mn, mx = _parse_word_count(value)
            if mn is not None: pq.word_count_min = mn
            if mx is not None: pq.word_count_max = mx

        elif canonical == "updated_after":
            d = _parse_date(value)
            if d: pq.updated_after = d

        elif canonical == "author":
            # Kept whole, spaces and all. The API matches it case-insensitively
            # against the entire author column, so a pen name with spaces needs
            # no quoting to work — quotes are still accepted, and are still the
            # way to stop a trailing shorthand word being eaten.
            pq.author = value
        elif canonical == "language":
            pq.language = value

        elif canonical == "sites":
            site = canonical_site(value)
            if site:
                pq.sites.append(site)
                # The token drives the chip the search bar renders, so it shows
                # the resolved archive rather than echoing what was typed —
                # which is how someone who typed `site:ff.net` learns it landed
                # on ffnet.
                token["value"] = site
            else:
                # Unrecognised archive: no filter and no chip. The span is
                # already consumed, so the words do not leak into the free-text
                # query either — `site:goodreads harry potter` searches every
                # archive for "harry potter" rather than searching none of them
                # for a site that does not exist here.
                continue

        elif canonical == "crossovers":
            v = value.lower()
            pq.crossovers = "only" if v in ("only", "yes", "true") else "exclude" if v in ("no", "false", "exclude") else "include"

        elif canonical == "in_series":
            v = value.lower()
            if v in ("true", "yes", "in", "series"):
                pq.in_series = True
                token["value"] = "true"
            elif v in ("false", "no", "standalone", "alone", "oneshot", "one-shot"):
                pq.in_series = False
                token["value"] = "false"

        pq.tokens.append(token)

    # ── 2. Strip consumed spans from text ──────────────────────────────────
    for start, end in sorted(consumed_spans, reverse=True):
        text = text[:start] + text[end:]

    # ── 3. Standalone shorthands in remaining text ──────────────────────────
    words = text.split()
    remaining = []

    for word in words:
        wl = word.lower().rstrip(".,")

        # Word count: >100k  <50k  200k+
        if re.match(r'^[><]=?[\d.]+[km]\+?$', wl, re.I) or re.match(r'^[\d.]+[km]\+$', wl, re.I):
            mn, mx = _parse_word_count(wl)
            if mn is not None: pq.word_count_min = mn
            if mx is not None: pq.word_count_max = mx
            pq.tokens.append({"key": "word_count", "value": word, "exclude": False, "raw": word})
            continue

        # Status
        if wl in STATUS_WORDS:
            pq.status = STATUS_WORDS[wl]
            pq.tokens.append({"key": "status", "value": pq.status, "exclude": False, "raw": word})
            continue

        # Rating
        if wl in RATING_WORDS:
            pq.ratings.append(RATING_WORDS[wl])
            pq.tokens.append({"key": "ratings", "value": RATING_WORDS[wl], "exclude": False, "raw": word})
            continue

        # An operator with nothing after it is an unfinished thought, not a
        # word to search for. Seen in the wild as:
        #
        #   fandom: fandom:Harry Potter tag:Female Harry Potter complete
        #
        # The search bar held a bare `fandom:`, the frontend serialiser writes
        # `cleanText + chips`, and the leftover was re-emitted in front of the
        # real operator. This parser then put "fandom:" into the free text, so
        # the query full-text matched that literal string and returned 51 works
        # instead of Harry Potter's 686,558 — silently, with no error and no
        # empty page to suggest anything had gone wrong.
        #
        # Checked against FIELD_ALIASES rather than any `\w+:`, because a bare
        # `\w+:` also matches "3:" and would quietly eat a word from a title
        # like "chapter 3: the return". A colon is ordinary punctuation
        # everywhere except after a word this parser recognises.
        bare = _BARE_OPERATOR_RE.match(word)
        if bare and FIELD_ALIASES.get(bare.group(1).lower()):
            continue

        remaining.append(word)

    pq.clean_text = " ".join(remaining).strip()
    return pq


def parsed_to_search_params(pq: ParsedQuery) -> dict:
    """Convert ParsedQuery into the dict that the search API expects."""
    def csv(lst): return ",".join(lst) if lst else None

    return {
        "q":                   pq.clean_text or None,
        "sites":               csv(pq.sites),
        "fandoms":             csv(pq.fandoms),
        "relationships":       csv(pq.relationships),
        "characters":          csv(pq.characters),
        "tags":                csv(pq.tags),
        "ratings":             csv(pq.ratings),
        "warnings":            csv(pq.warnings),
        "categories":          csv(pq.categories),
        "exclude_fandoms":     csv(pq.exc_fandoms),
        "exclude_relationships": csv(pq.exc_relationships),
        "exclude_characters":  csv(pq.exc_characters),
        "exclude_tags":        csv(pq.exc_tags),
        "status":              pq.status,
        "author":              pq.author,
        "language":            pq.language,
        "word_count_min":      pq.word_count_min,
        "word_count_max":      pq.word_count_max,
        "updated_after":       pq.updated_after,
        "crossovers":          pq.crossovers,
        "in_series":           pq.in_series,
    }


# ── The inverse: filters back into the bar's own syntax ───────────────────────
#
# Written for the traffic log, and the gap it closes is large. The search
# middleware recorded a search only `if q`, so a search made entirely from the
# filter panel — every fandom hub, every ship hub, every fandom, character or
# tag clicked on a result card — was never recorded at all. Measured over 24h of
# origin logs: 38 searches reached /api/search, 16 carried `q`, and the other 22
# were invisible. The most common way people use this site was the way the
# report could not see.
#
# The serialisation is the SEARCH BAR's, not an invention, and that is the whole
# point: the bar mirrors the filter panel (serializeFiltersToQuery in
# frontend/app/page.tsx), so `fandom:Naruto complete` is literally the text the
# reader had in front of them. It also round-trips — paste a recorded row back
# into the bar and the same search runs — which a bespoke "fandoms=Naruto"
# rendering would not.
#
# Mirrored by hand against the TypeScript, like the two parsers above, and for
# the same reason: there is no shared source and drifting is the failure mode.
# If you add an operator to one, add it here.
_ARRAY_FIELDS = (
    ("fandoms", "fandom"), ("relationships", "ship"), ("characters", "char"),
    ("tags", "tag"), ("sections", "subsite"), ("warnings", "warn"),
    ("categories", "cat"),
)
_EXCLUDE_FIELDS = (
    ("exclude_fandoms", "-fandom"), ("exclude_relationships", "-ship"),
    ("exclude_characters", "-char"), ("exclude_tags", "-tag"),
)
_ALL_SITES = {"ao3", "ffnet", "fictionalley"}


def _quote(value: str) -> str:
    """Quote only where the value would not survive the trip back through the
    parser. Same three rules as needsQuoting() in the TypeScript: an embedded
    quote, a trailing shorthand word, or something that reads as `key:`."""
    v = (value or "").strip()
    if not v:
        return v
    if '"' in v:
        return '"%s"' % v.replace('"', "")
    words = v.split()
    if len(words) > 1 and _SHORTHAND_RE.match(words[-1]):
        return '"%s"' % v
    for m in re.finditer(r"(?:^|\s)-?(\w+)\s*:", v):
        if FIELD_ALIASES.get(m.group(1).lower()):
            return '"%s"' % v
    return v


def _words(n: int) -> str:
    """100000 -> 100k, 1000000 -> 1m. The parser understands k/m suffixes and
    not raw digits, so this is what round-trips."""
    return f"{n // 1_000_000}m" if n % 1_000_000 == 0 else f"{round(n / 1000)}k"


def serialise_filters(params) -> str:
    """Render the filter half of a search as the search bar would show it.

    `params` is anything with .getlist()/.get() — a Starlette QueryParams.
    Returns "" when nothing narrows the search, which is the signal not to
    record it: a browse of the whole index with no filter and no text is not a
    query anybody could act on in a report, and an empty string in the `q`
    column would group every one of them into a single meaningless row.
    """
    def many(key):
        try:
            return [v for v in params.getlist(key) if (v or "").strip()]
        except AttributeError:
            v = params.get(key)
            return [v] if v else []

    def one(key):
        return (params.get(key) or "").strip()

    # A parameter may arrive either repeated (?tags=a&tags=b) or comma-joined
    # (?tags=a,b) — the frontend sends the second, links in the wild send both.
    def values(key):
        out = []
        for raw in many(key):
            out.extend(p.strip() for p in raw.split(",") if p.strip())
        return out

    parts: list[str] = []
    for key, op in _ARRAY_FIELDS:
        for v in values(key):
            parts.append(f"{op}:{_quote(v)}")
    for key, op in _EXCLUDE_FIELDS:
        for v in values(key):
            parts.append(f"{op}:{_quote(v)}")

    # Ratings only when they are a real narrowing: every rating selected IS the
    # default, and spelling it out would put four operators in front of every
    # search. `explicit=true` widens the default set to five.
    ratings = values("ratings")
    full = 5 if one("explicit") == "true" else 4
    if ratings and len(ratings) < full:
        parts.extend(f"rating:{r}" for r in ratings)

    status = values("status")
    if len(status) == 1:
        parts.append("complete" if status[0] == "complete" else "wip")

    lo, hi = one("word_count_min"), one("word_count_max")
    try:
        if lo and hi:
            parts.append(f"words:{_words(int(lo))}-{_words(int(hi))}")
        elif lo:
            parts.append(f"words:>{_words(int(lo))}")
        elif hi:
            parts.append(f"words:<{_words(int(hi))}")
    except ValueError:
        pass

    if one("language"):
        parts.append(f"lang:{_quote(one('language'))}")
    if one("author"):
        parts.append(f"author:{_quote(one('author'))}")
    if one("updated_after"):
        parts.append(f"updated:{one('updated_after')}")
    if one("in_series") == "true":
        parts.append("series:true")
    elif one("in_series") == "false":
        parts.append("series:false")

    # Sites only when fewer than all three, for the same reason as ratings.
    sites = [s for s in values("sites") if s in _ALL_SITES]
    if sites and len(sites) < len(_ALL_SITES):
        parts.extend(f"site:{s}" for s in sites)

    xover = one("crossovers")
    if xover in ("only", "exclude"):
        parts.append(f"xover:{xover}")

    return " ".join(parts)
