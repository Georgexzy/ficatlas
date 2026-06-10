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
  site:ao3                     site:ffnet

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

OPERATOR_RE = re.compile(
    rf'(-?)(\w+):{_VAL}',
    re.IGNORECASE,
)

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

# Field aliases → canonical name
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
    "crossover": "crossovers", "xover": "crossovers",
    "warn": "warnings", "warning": "warnings",
    "category": "categories", "cat": "categories",
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
    word_count_min: Optional[int]  = None
    word_count_max: Optional[int]  = None
    updated_after: Optional[str]   = None
    crossovers: Optional[str]      = None  # include | exclude | only

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

    for m in OPERATOR_RE.finditer(raw):
        exclude_flag = m.group(1) == "-"
        key_raw      = m.group(2).lower()
        value        = m.group(3) if m.group(3) else m.group(4)  # quoted or bare

        canonical = FIELD_ALIASES.get(key_raw)
        if not canonical:
            continue  # unknown operator — leave in text

        consumed_spans.append((m.start(), m.end()))

        token = {"key": canonical, "value": value, "exclude": exclude_flag, "raw": m.group(0)}

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

        elif canonical == "language":
            pq.language = value

        elif canonical == "sites":
            pq.sites.append(value.lower())

        elif canonical == "crossovers":
            v = value.lower()
            pq.crossovers = "only" if v in ("only", "yes", "true") else "exclude" if v in ("no", "false", "exclude") else "include"

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
        "language":            pq.language,
        "word_count_min":      pq.word_count_min,
        "word_count_max":      pq.word_count_max,
        "updated_after":       pq.updated_after,
        "crossovers":          pq.crossovers,
    }
