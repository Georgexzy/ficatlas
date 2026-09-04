"""What a reader typed, as against what a search engine wants to be given.

`query_parser.py` handles the syntax this site invented — `fandom:`, `>100k`,
`-tag:fluff`. This module handles the syntax fandom invented, which nobody
taught anybody and everybody uses:

    long drarry fics
    looking for a fic where harry raises teddy
    fics where harry is a slytherin
    wandcrafter harry
    severitus

Recorded searches are overwhelmingly this shape, and the search path handled it
badly for one specific reason: `websearch_to_tsquery` ANDs every term, so each
word of framing is a hard filter over the whole index. Measured on the live
index before this module existed:

    drarry                 5,000 (the ceiling)
    long drarry fics          68

Two words that carry no information about any story removed 98.6% of the
answer, and the reader is shown 68 results rather than an error — the failure
reads as "the index does not have this".

Three things happen here, in order.

1. FRAMING IS REMOVED. "looking for", "fics where", "recs", "on AO3", a
   trailing "please". Dropping a term from an AND-query can only WIDEN the
   result set, so this direction is safe by construction: the worst case is
   that ranking has one less coincidence to reward.

2. QUALIFIERS BECOME FILTERS. "long" means a word count, not a word to find.
   This direction NARROWS, so unlike framing it is gated on the query actually
   being a request — i.e. on the raw text carrying framing at all. `The Long
   Way Home` is a title and keeps its "long"; `long drarry fics` is a request
   and gets `word_count >= 50k`.

3. THE PHRASE IS RESOLVED AGAINST THE TAG VOCABULARY. This is the part that
   generalises past Harry Potter, and it needs no dictionary: fandom already
   wrote one. `facets` holds 1.57M freeform tags with work counts, so
   "harry raises teddy" resolves to `Harry Potter Raises Teddy Lupin` (208
   works) and "coffee shop" to `Alternate Universe - Coffee Shops & Cafés`
   (20,078) by the same lookup, in any fandom, with no per-fandom code.
   Verified across Naruto, My Hero Academia, Marvel, Supernatural, Teen Wolf,
   Avatar, Star Wars, Tolkien, BTS, Haikyuu and Attack on Titan.

Where the patterns come from
----------------------------
Not invention. The two long-running Harry Potter fic-finder communities on
LiveJournal (hpficfinders, potterficfinder) put the entire request in the post
title, so their subject lines are a corpus of how people ask:

    "Fic search: Hermione's parents kidnapped a girl"
    "Looking for an old Snarry fanfiction"
    "Help! I'm looking for a deleted Harry/Draco story on AO3"
    "Searching for a specific drarry fic"
    "Dumbledore takes Harry's place at Dursley's--doesn't turn out well"

The FORM is not fandom-specific — only the nouns inside it are — which is why
the pattern list is written once and never per fandom. Adding a shape here is
cheap; adding one nobody actually types is not, because every pattern is
another way for a real title to lose a word.

The curated alias table is deliberately SMALL, and only covers the case the
vocabulary cannot: a reader's word that does not appear in the archive's word
at all ("wandcrafter" for `Wandmaker`). Anything where the reader's words are
IN the tag needs no entry and must not get one.
"""

import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text as sql_text

# ── Framing ──────────────────────────────────────────────────────────────────
#
# Removed wherever they appear. Longest-first: `\bfics?\b` would otherwise eat
# the "fic" out of "looking for a fic where" and leave the rest of the frame
# standing.
#
# "story"/"stories" is the one word here that is regularly part of a real title
# (Toy Story, Ghost Story), so it is ONLY removed as part of a phrase that is
# unambiguously a request — "stories where", "any story about". The bare word is
# left alone. "fic", "fanfic" and "recs" have no such problem and are removed on
# sight.
_FRAME_PATTERNS = [
    # The subject lines people actually write. Taken from the two long-running
    # HP fic-finder communities (hpficfinders and potterficfinder on
    # LiveJournal), where the request is the post title and so has to carry the
    # framing in the same breath as the query:
    #
    #   "Fic search: Hermione's parents kidnapped a girl"
    #   "Looking for an old Snarry fanfiction"
    #   "Help! I'm looking for a deleted Harry/Draco story on AO3"
    #   "Searching for a specific drarry fic"
    #   "Help finding a fanfic"
    #
    # The form is not fandom-specific — only the nouns inside it are — which is
    # why this set is written once and never per fandom.
    r"^\s*(?:help\s*[!.]*\s*)?(?:i(?:\s*'?\s*m|\s+am)?\s+)?"
    # "for" is optional: "Searching fic Sam dean fight / dean hurt" is a real
    # subject line, and so is "Looking specific deleted j2 fic".
    r"(?:looking|searching|hunting)(?:\s+for)?\b",
    r"^\s*(?:does\s+)?any(?:one|body)\s+(?:know|have|remember|recall|got)\b"
    r"(?:\s+(?:of|about|a|an|any))*",
    r"^\s*(?:can|could|would)\s+(?:any(?:one|body)|you|somebody|someone)\s+"
    r"(?:rec\w*|help|suggest|point)\b(?:\s+me)?",
    r"^\s*(?:help\s*[!.]*\s*)?(?:me\s+)?(?:trying\s+to\s+find|find\s+me|finding)\b",
    r"^\s*(?:fic|fanfic|story|rec)\s*(?:search|finder|find|request)\s*:?",
    # "SF:", "LF:", "FF:" — the finder communities' own shorthand for a post
    # that is a search, and "FOUND!" for one that has been answered.
    r"^\s*(?:sf|lf|ff|ffs)\s*:",
    r"^\s*found\s*[!:]+",
    r"^\s*in\s+search\s+of\b",
    r"^\s*lf\b",
    r"^\s*(?:tomt|ficfinder)\b",
    r"^\s*rec(?:s|ommendations?)?\b(?:\s+(?:for|me))*",
    # "the one where sirius…" — the whole request is a relative clause.
    r"^\s*the\s+one\s+(?:where|in\s+which|with)\b",
    # The fic-noun, with the relative clause that usually follows it. Both
    # halves go: "where"/"about" are English stopwords to the tsquery anyway,
    # but they must not survive into the tag lookup in step 3.
    # The fic-noun, with any count in front of it and the relative clause that
    # usually follows. "Looking for two fanfics -protective john", "Looking for
    # three specific fics", "Looking for a few fics! Omega!Dean, Whump!Dean" —
    # the number is how many answers are wanted, never a word in any of them.
    r"\b(?:(?:\d+|a\s+few|one|two|three|four|five|several|multiple)\s+)?"
    r"(?:fan\s?)?fic(?:s|tions?)?\b"
    r"(?:\s+(?:where|in\s+which|about|with|featuring|involving|that|in))?",
    r"\bstor(?:y|ies)\b\s+(?:where|in\s+which|about|with|featuring|involving|that)\b",
    r"^\s*(?:a|any|some)\s+stor(?:y|ies)\b",
    r"\b(?:please|pls|plz|thanks|thank\s+you|thx|ty|etc\.?)\s*$",
]

# Words that describe the REQUEST rather than the story, and only ever appear
# because somebody is asking for help: "an old Snarry fanfiction", "a specific
# drarry fic", "a deleted Harry/Draco story". Each is a real word that could be
# in a title, so they are gated on the query being a request — the same gate the
# length words are behind, for the same reason.
_REQUEST_ADJECTIVES = re.compile(
    r"\b(?:old|specific|certain|deleted|lost|missing|forgotten|half[- ]remembered"
    # How many answers are wanted, never a word in any of them: "Looking for
    # two fanfics", "Looking for three specific fics", "a few fics". "one" is
    # NOT here — "one shot", "the one where", "Chapter One" are all content.
    r"|two|three|four|five|several|multiple|few"
    # The artefact, when it survives as a bare noun: "Looking for
    # depressed/tied up Sam story". Already excluded from the tag lookup by
    # _STOPWORDS, but it still AND-ed into the tsquery, where every result had
    # to contain the word "story".
    r"|stor(?:y|ies)|fanfic(?:s|tions?)?)\b",
    re.I)

# "…on AO3", "…on ff.net". Naming the archive in passing is not a search term —
# it is the `site:` filter, and left in the text it is one more word that every
# result has to contain.
_SITE_PHRASE = re.compile(
    r"\bon\s+(ao3|a03|archive\s+of\s+our\s+own|archiveofourown(?:\.org)?"
    r"|ff\.?net|ffn|fanfiction(?:\.net)?|fiction\s?alley|ficalley)\b", re.I)
_FRAME_RES = [re.compile(p, re.I) for p in _FRAME_PATTERNS]


# ── Qualifiers ───────────────────────────────────────────────────────────────
#
# (pattern, word_count_min, word_count_max, chip label, needs_request_register).
#
# The thresholds are the fandom conventions, not this index's percentiles. The
# index median is 2,321 words and its 99th percentile is 104,097, so a
# distribution-derived "long" would mean "top 1%" — which is not what somebody
# asking for a long fic means. 50k is the usual reader line for a long work and
# 100k for an epic; a one-shot is anything that did not need a second chapter.
#
# `long fic` / `longfic` carry their own register, so they fire unconditionally.
# The bare adjectives do not, and are gated: "short" and "long" are ordinary
# title words.
_QUALIFIERS = [
    (r"\b(?:epic|novel)[-\s]length\b",      100_000, None,   ">100k", False),
    (r"\bmonster\s+fics?\b",                100_000, None,   ">100k", False),
    (r"\blong\s*fics?\b",                    50_000, None,    ">50k", False),
    (r"\bdrabbles?\b",                         None, 2_000,   "<2k",  False),
    (r"\bone[-\s]?shots?\b",                   None, 10_000,  "<10k", False),
    (r"\bepic\b",                           100_000, None,   ">100k", True),
    (r"\blengthy\b",                         50_000, None,    ">50k", True),
    (r"\blong\b",                            50_000, None,    ">50k", True),
    (r"\bshort\b",                             None, 10_000,  "<10k", True),
]
_QUALIFIER_RES = [(re.compile(p, re.I), mn, mx, label, gated)
                  for p, mn, mx, label, gated in _QUALIFIERS]

# Status words the syntax parser does not already know. `complete`, `completed`,
# `wip`, `incomplete` and `ongoing` are handled there as ungated shorthand;
# these are the natural-language spellings, and they ARE gated because
# "finished" and "abandoned" both turn up in titles.
_STATUS_PATTERNS = [
    (r"\b(?:finished|all\s+done)\b", "complete"),
    (r"\b(?:unfinished|in\s+progress|still\s+updating|not\s+finished)\b", "in_progress"),
]
_STATUS_RES = [(re.compile(p, re.I), v) for p, v in _STATUS_PATTERNS]


# ── Tag resolution ───────────────────────────────────────────────────────────

# Words that carry no meaning in a tag lookup. Deliberately the English closed
# class plus the handful of relative pronouns a request frame leaves behind —
# NOT a general stopword list, because content words must survive even when they
# are common ("dark", "good", "bad" are all real tag words).
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "into", "about", "as", "is", "are", "was",
    "were", "be", "been", "being", "am", "do", "does", "did", "has", "have",
    "had", "will", "would", "can", "could", "should", "it", "its", "this",
    "that", "these", "those", "there", "here", "where", "which", "who", "whom",
    "what", "when", "how", "why", "not", "no", "any", "some", "all", "both",
    "each", "very", "just", "also", "than", "then", "so", "such", "own",
    "get", "gets", "got", "become", "becomes", "i", "me", "my", "we", "us",
    "you", "your", "he", "him", "his", "she", "her", "they", "them", "their",
    # Words for the ARTEFACT, never for what is in it. They survive the framing
    # pass when they are not part of a request phrase ("a Harry/Draco story on
    # AO3" leaves a bare "story"), and a bare "story" resolved through the stem
    # `stor` to `Storytelling` and `Storybrooke` — a 2,120-work tag branch and a
    # category promotion, from a word the reader used to mean "fanfic".
    "story", "stories", "fic", "fics", "fanfic", "fanfics", "fanfiction",
    "book", "books", "chapter", "chapters", "part", "parts", "work", "works",
}

# A tag has to be carried by this many works before it is worth widening a
# search with. Below it the "tag" is usually one author's private joke, and the
# OR branch costs a GIN lookup for nothing.
TROPE_TAG_MIN_WORKS = int(os.getenv("TROPE_TAG_MIN_WORKS", "10"))

# A ONE-WORD trope has to be a much better known one. A single word matches a
# great many tags, and the ones it matches at 16 and 19 works are noise
# ("traveller - Freeform", "Birth Defects"); the one-word tropes that are worth
# resolving are the ones everybody has heard of, and those are all large —
# Omegaverse (9,489), Soulmates (41,489), Slow Burn (193,882).
TROPE_TAG_MIN_WORKS_SOLO = int(os.getenv("TROPE_TAG_MIN_WORKS_SOLO", "200"))

# How many spellings of the resolved trope to admit. The same trope is written
# several ways ("coffee shop AU", "Coffeeshop AU", "Alternate Universe - Coffee
# Shops & Cafés") and the point is to catch all of them; past half a dozen the
# tail is other tropes that merely share a word.
TROPE_TAG_CAP = int(os.getenv("TROPE_TAG_CAP", "6"))

# A tag longer than this is a sentence somebody typed into the tag box, not a
# trope. They match a lot of word pairs and mean nothing.
_TROPE_TAG_MAX_LEN = 60

# How many works an UNBOUNDED tag branch may cover before it stops being worth
# adding at all.
#
# The branch earns its place by finding works the text match cannot see, and it
# is AND-ed with whatever words the tag did not account for. With no such words
# there is nothing to bound it, and a very large tag then contributes an OR over
# hundreds of thousands of rows that the text match had already matched —
# because a work tagged `Hurt/Comfort` has "hurt" and "comfort" in its document
# by definition. Measured: "hurt comfort" resolves `Hurt/Comfort` (577,244
# works) with no leftover and hit the 20s statement timeout and a 503; the same
# tag with one word left over to bound it ("hurt comfort geralt") returns in
# 3.4s.
#
# The cap applies ONLY when there is no leftover. 100,000 sits well above the
# largest trope that is still a useful whole-query answer (`Time Travel`,
# 45,960) and below the two that are not (`Slow Burn` 193,882, `Hurt/Comfort`
# 577,244). The resolution itself is kept either way — it still tells the ranker
# this is a category query, which costs nothing.
TROPE_BRANCH_MAX_WORKS = int(os.getenv("TROPE_BRANCH_MAX_WORKS", "100000"))

_TROPE_SQL_CACHE: dict[tuple, tuple[list[tuple[str, int]], float]] = {}
_TROPE_TTL = float(os.getenv("TROPE_TAG_CACHE_SEC", "600"))

# Negation words. A tag saying the OPPOSITE of the query still contains every
# word of it — "Alternate Universe - No Time Travel" matches a lookup for "time
# travel" — so a candidate may only carry one of these if the reader did.
_NEGATIONS = ("no", "not", "anti", "without", "never", "non")

_NEGATION_RE = re.compile(r"\b(?:" + "|".join(_NEGATIONS) + r")\b[\s-]", re.I)

# Tags that name another WORK rather than describe this one. They are the reason
# a famous TITLE looks like a trope: `all the young dudes` is a 13-work tag and
# `Inspired by All the Young Dudes - MsKingBean89` is a 225-work one, so without
# this the most-searched title on the site resolved to "a kind of story" and got
# ranked as one — the exact failure the ranking notes in api/search.py describe
# in the other direction.
_REFERENCE_TAG_RE = re.compile(
    r"^\s*(?:inspired\s+by|based\s+(?:on|off)|podfic(?:\s+of)?|translation\s+of"
    r"|remix\s+of|sequel\s+to|prequel\s+to|fanfic\s+of|fanart\s+of|art\s+for"
    r"|song|book|movie|episode|chapter)\b[:\s]", re.I)

# Kill switches, for the same reason SEARCH_SHIP_ALIASES has one: this sits on
# the hottest path on the site, adds an OR to its main predicate and a
# vocabulary lookup in front of it. SEARCH_QUERY_INTENT=false takes the whole
# module out of the request without a deploy; SEARCH_TROPE_TAGS=false leaves the
# framing and length reading in and drops only the tag branch.
def _flag(name: str) -> bool:
    return os.getenv(name, "true").lower() not in ("0", "false", "no")


QUERY_INTENT_ON = _flag("SEARCH_QUERY_INTENT")
TROPE_TAGS_ON = _flag("SEARCH_TROPE_TAGS")

# Kinds that mean the reader named a THING, not a kind of thing. A query that is
# exactly a fandom, a ship or a character is already served by the facet path and
# must not also grow a tag branch: "toy story" is a 1,473-work FANDOM, and
# resolving it to the 46 works tagged `Alternate Universe - Toy Story Fusion`
# replaced the fandom with fan-works of it in other fandoms.
_THING_KINDS = ("fandom", "fandom_ao3", "relationship", "character")

_TOP_FACET_SQL = sql_text("""
    SELECT kind, count FROM facets
     WHERE lower(value) = :v
     ORDER BY count DESC
     LIMIT 1
""")

_THING_CACHE: dict[str, tuple[bool, float]] = {}


def _names_a_thing(db, phrase: str) -> bool:
    """Is this phrase, verbatim, a fandom / ship / character the index knows?

    One btree probe on ix_facets_value_lower, cached — the same handful of
    fandom names get typed over and over.
    """
    hit = _THING_CACHE.get(phrase)
    if hit and time.monotonic() - hit[1] < _TROPE_TTL:
        return hit[0]
    try:
        row = db.execute(_TOP_FACET_SQL, {"v": phrase}).fetchone()
    except Exception:
        db.rollback()
        return False
    answer = bool(row and row[0] in _THING_KINDS and row[1] >= 100)
    if len(_THING_CACHE) > 4096:
        _THING_CACHE.clear()
    _THING_CACHE[phrase] = (answer, time.monotonic())
    return answer


# Reader shorthand whose letters do not appear in the archive's word for the
# same thing. THIS IS THE ONLY CASE THAT BELONGS HERE: if the reader's words are
# already inside the tag, the vocabulary lookup finds it and an entry would only
# add a way to be wrong.
#
# Values are the archive's own words, best first. They are substituted into the
# text and the result is searched ALONGSIDE what the reader typed, not instead
# of it — see `_alias_expand`.
#
# The several spellings matter more than they look. "wandcrafter harry" found 3
# works on its own; the trope is filed under `Wandmaker Harry Potter` on AO3 and
# written "wandmaker", "wandcrafting" or "wandlore" in the summaries — and a
# summary is the ONLY place an FF.net work can say it, because FF.net has no
# freeform tags at all (its `tags` array holds provenance markers like
# `ffnet_dump`). So a tag-only resolution reaches the AO3 half of the index and
# nothing else. Searching the archive's words as TEXT is what reaches the rest:
# 3 works became 205, including a 6,957-kudos work and 40 on FF.net.
_TROPE_ALIASES = {
    "wandcrafter": ["wandmaker", "wandcrafting", "wandlore"],
    "wandcrafting": ["wandmaking", "wandmaker", "wandlore"],
    "e2l": ["enemies to lovers"],
    "f2l": ["friends to lovers"],
    "abo": ["omegaverse", "alpha beta omega"],
    "a/b/o": ["omegaverse", "alpha beta omega"],
    "h/c": ["hurt comfort"],
    "mod": ["master of death"],
    "peggy sue": ["time travel fix it", "time travel"],
    "coffeeshop": ["coffee shop"],
    # Two initialisms that are almost always written together, so the pair is
    # its own key — sorted longest-first, "si oc" is tried before either half
    # and resolves to one tag instead of two competing ones.
    "si oc": ["self insert"],
    "oc si": ["self insert"],
    "si": ["self insert"],
    "oc": ["original character"],
    "ewe": ["epilogue what epilogue"],
}

# How many rewritten spellings may be searched beside the reader's own. Each one
# is another OR branch on the hottest predicate on the site, and the third
# spelling of anything is already the long tail.
ALIAS_VARIANT_CAP = int(os.getenv("TROPE_ALIAS_VARIANT_CAP", "3"))

# An alias short enough to collide with an ordinary word WIDENS the search but
# does not get a vote on the ranking. "mod" is a real piece of fandom shorthand
# (MoD!Harry) and also the title word of 242 works in this index, most of them
# about Minecraft; promoting "mod squad" to a category query on that evidence
# would rank a title search by readership. Four characters is the line because
# nothing below it is a coinage — abo, h/c, e2l, si, oc are initialisms, while
# "wandcrafter" and "peggy sue" are words fandom made up and nobody titles a
# work after.
#
# Measured on the LONGEST WORD of the key, not on the key's length: "si oc" is
# five characters and two initialisms, and string length would have promoted it.
_ALIAS_MIN_LEN_FOR_CATEGORY = 4

_ALIAS_RES = [(re.compile(r"(?<![\w/])" + re.escape(k) + r"(?![\w/])", re.I), k, v)
              for k, v in sorted(_TROPE_ALIASES.items(), key=lambda kv: -len(kv[0]))]


def _alias_expand(text: str) -> tuple[list[str], bool]:
    """The same query in the archive's words. Returns (variants, is_shorthand).

    One alias per query, like the ship-nickname path: two would be a question
    about two tropes at once, which the ordinary text search answers better
    than a guess would.

    `is_shorthand` says the reader used a coined word rather than a phrase the
    archive would recognise — which is itself evidence that they asked for a
    KIND of story. Nobody titles a work "wandcrafter harry". See
    _ALIAS_MIN_LEN_FOR_CATEGORY for the aliases that are deliberately excluded
    from that inference.
    """
    for rx, key, replacements in _ALIAS_RES:
        if not rx.search(text):
            continue
        out: list[str] = []
        for r in replacements[:ALIAS_VARIANT_CAP]:
            variant = rx.sub(r, text)
            if variant != text and variant not in out:
                out.append(variant)
        if out:
            longest = max((len(w) for w in key.split()), default=0)
            return out, longest >= _ALIAS_MIN_LEN_FOR_CATEGORY
    return [], False


@dataclass
class Intent:
    """The reader's query, restated in terms the search path can use."""
    text: str = ""                      # free text with the framing taken out
    word_count_min: Optional[int] = None
    word_count_max: Optional[int] = None
    status: Optional[str] = None
    site: Optional[str] = None          # "…on AO3" named an archive, not a word
    tags: list[str] = field(default_factory=list)   # tag spellings to OR in
    tag_works: int = 0                  # works behind the best of them
    tag_leftover: str = ""              # words the tag did NOT account for
    tag_is_whole: bool = False          # the tag consumed every content word
    tag_branch_ok: bool = False         # worth OR-ing into the predicate at all
    text_variants: list[str] = field(default_factory=list)  # same query, archive's words
    is_shorthand: bool = False          # the reader used a coined word, so: a category
    tokens: list[dict] = field(default_factory=list)  # chips for the UI


def _is_request(raw: str) -> bool:
    """Did the reader frame this as a request for stories rather than name one?

    Asked before anything is removed, and separately from removing it, because
    the two answers have to come from the same text: `long fics` loses its
    "fics" to the framing pass, and a register test run afterwards would then
    read "long" as an ordinary word in an ordinary title.
    """
    return any(rx.search(raw) for rx in _FRAME_RES)


def _strip_frames(text: str) -> str:
    """Take the request framing out.

    Never strips to nothing: a search for the literal word "fanfiction" is a
    search, and an empty query would silently become a browse of the whole
    index.
    """
    for rx in _FRAME_RES:
        stripped = rx.sub(" ", text)
        if stripped != text and stripped.strip():
            text = stripped
    return re.sub(r"\s+", " ", text).strip()


def read_request(raw: str) -> Intent:
    """The pure half: framing, length, status and archive. No database.

    Order matters at every step and each one is load-bearing:

      1. REGISTER, off the raw string. "long fics" loses its "fics" to the
         framing pass, and a register test run afterwards would then read
         "long" as an ordinary word in an ordinary title.
      2. QUALIFIERS, before the framing goes, so a length word and the fic-noun
         beside it are still adjacent: "monster fics" is one phrase meaning
         100k+, and taking "fics" out first leaves a bare "monster".
      3. THE ARCHIVE, before the framing goes, because the frame patterns eat
         the "fanfiction" out of "on fanfiction.net".
      4. FRAMING.
      5. What framing STRANDS — request adjectives, counts, a bare artefact
         noun, leading punctuation. These only ever appear because the phrase
         around them was removed, so they are gated on the same register.
    """
    intent = Intent(text=raw)
    text, is_request = raw, _is_request(raw)

    for rx, mn, mx, label, gated in _QUALIFIER_RES:
        if gated and not is_request:
            continue
        m = rx.search(text)
        if not m:
            continue
        candidate = re.sub(r"\s+", " ", rx.sub(" ", text)).strip()
        # A qualifier that IS the whole query is a browse, not a filter with
        # nothing to filter — "one shot" alone should search for the phrase.
        if not candidate:
            continue
        text = candidate
        if mn is not None and intent.word_count_min is None:
            intent.word_count_min = mn
        if mx is not None and intent.word_count_max is None:
            intent.word_count_max = mx
        intent.tokens.append({"key": "word_count", "value": label,
                              "exclude": False, "raw": m.group(0)})

    for rx, value in _STATUS_RES:
        if not is_request:
            continue
        m = rx.search(text)
        if not m:
            continue
        candidate = re.sub(r"\s+", " ", rx.sub(" ", text)).strip()
        if not candidate or intent.status:
            continue
        text = candidate
        intent.status = value
        intent.tokens.append({"key": "status", "value": value,
                              "exclude": False, "raw": m.group(0)})

    # "…on AO3" is a filter, not a word every result has to contain. Read before
    # the framing goes, because the frame patterns eat the "fanfiction" out of
    # "on fanfiction.net" and would leave a bare "on .net" behind.
    m = _SITE_PHRASE.search(text)
    if m:
        from query_parser import canonical_site
        named = " ".join(m.group(1).lower().split())
        # Both spellings, because SITE_ALIASES holds some with dots
        # ("archiveofourown.org", "ff.net") and some without ("ffnet",
        # "fanfictionnet"), and a reader types whichever they saw.
        site = canonical_site(named) or canonical_site(named.replace(".", ""))
        candidate = re.sub(r"\s+", " ", text[:m.start()] + " " + text[m.end():]).strip()
        if site and candidate:
            text, intent.site = candidate, site
            intent.tokens.append({"key": "sites", "value": site,
                                  "exclude": False, "raw": m.group(0)})

    text = _strip_frames(text)

    if is_request:
        candidate = re.sub(r"\s+", " ", _REQUEST_ADJECTIVES.sub(" ", text)).strip()
        if candidate:
            text = candidate
        # Punctuation stranded by everything above — "Looking for a few fics!
        # Omega!Dean" leaves a leading "!". Harmless to the tsquery, but it is
        # not harmless to the exact-title bonus, which compares the whole
        # string.
        candidate = text.strip(" ,;:!?-–—")
        if candidate:
            text = candidate

    # "any fics where thorin lives" leaves a stranded "any". Harmless to the
    # tsquery (it is an English stopword) but it is not harmless to the tag
    # lookup or to the exact-title bonus, and it only ever appears because the
    # frame in front of it was removed. Gated on that, so `The Long Way Home`
    # keeps its article.
    if is_request:
        text = re.sub(r"^(?:a|an|any|the|some)\s+", "", text, flags=re.I).strip() or text

    intent.text = text
    return intent


def _trope_tokens(text: str) -> list[str]:
    """Content words for the vocabulary lookup.

    `dark!harry` is split on the bang, because the bang is fandom's own
    modifier syntax and the archives write the same thing with a space.
    """
    words = [w for w in re.split(r"[^\w']+", text.lower(), flags=re.UNICODE) if w]
    out: list[str] = []
    for w in words:
        # Deduplicated: a rewritten spelling can repeat a word ("ewe" becomes
        # "epilogue what epilogue"), and the same word twice is one condition
        # asked twice and a coverage denominator counted once too often.
        if len(w) >= 3 and w not in _STOPWORDS and w not in out:
            out.append(w)
    return out


# `value ~* '\mword'` — the word must START somewhere in the tag, not merely
# appear inside it. A plain `ILIKE '%long%'` matched "along", which is how
# `the long way home` resolved to `this took way too long to write`; and it
# still matches "Shops" for "shop", which a whole-word boundary would not. The
# trigram index serves the regex (pg_trgm extracts trigrams from it), and
# measured on the live vocabulary it is FASTER than the ILIKE it replaces —
# 4.7ms against 21ms — because it narrows the bitmap before the heap recheck.
_TROPE_SQL_TMPL = """
    SELECT value, count FROM facets
     WHERE kind = 'tag'
       AND count >= :min_works
       AND length(value) <= :max_len
       AND {clauses}
     ORDER BY count DESC
     LIMIT :cap
"""

# Structural furniture AO3 wraps a tag in. None of it is part of the trope's
# name, and all of it dilutes the coverage test below: `Alternate Universe -
# Coffee Shops & Cafés` is a two-word trope wearing a four-word costume.
_AO3_WRAPPER_RES = [
    re.compile(r"\s+-\s+(?:freeform|relationship|character|fandom)\s*$", re.I),
    re.compile(r"\s*\([^)]*\)\s*$"),
    re.compile(r"^(?:alternate\s+universe|au)\s*-\s*", re.I),
]

# How much of the tag the reader's words have to account for.
#
# The window lookup asks "does this tag contain these words"; that is enough to
# find `Slytherin Harry Potter` from "harry slytherin", and not nearly enough to
# reject `this took way too long to write` from "the long way home" — a real
# 23-work tag that contains both words and means nothing. Coverage is the
# difference: two words out of that tag's five content words is 0.4, while
# `Slytherin Harry Potter` is 0.67 and `Time Travel` is 1.0. The line sits just
# above one-half deliberately: the reader's words have to be MOST of the tag,
# which is what separates `Harry Potter Raises Teddy Lupin` (0.6) from
# `Time Travelling Karl Jacobs` (0.5).
#
# The parenthetical is stripped BEFORE the check and every window word must
# still be found, which is what drops `Dark Mark (Harry Potter)` from
# "dark!harry": "harry" only ever matched the fandom disambiguator, not the
# trope.
TROPE_TAG_MIN_COVERAGE = float(os.getenv("TROPE_TAG_MIN_COVERAGE", "0.55"))

# The lookup asks for more rows than it keeps, because coverage is applied after
# it: filtering first and then taking the top six would return six of whatever
# survived rather than the six most-used tags that survived.
_TROPE_SQL_LIMIT = 24


# Auxiliaries that lose an apostrophe when typed in a hurry. "anakin doesnt
# fall" has to reach `Anakin Skywalker Doesn't Fall`, and no suffix rule gets
# there — the letters simply differ.
_CONTRACTION_STEMS = {"does", "do", "wo", "ca", "is", "are", "was", "were",
                      "did", "would", "could", "should", "has", "have", "ai"}

# Suffixes stripped before matching a word against the vocabulary. Deliberately
# a short, safe list rather than a real stemmer: the archives inflect the same
# trope every way round — `Sirius Black Raises Harry Potter`,
# `Harry Potter was Raised by Sirius Black`, `Raising Harry` — and a reader
# types whichever one they think in. Matching the literal word found the 51-work
# spelling and missed the 236-work one.
#
# `er`/`ers` are NOT here and must not be: they turn "traveller" into "travell"
# and "master" into "mast", which stops matching the words they came from.
_SUFFIXES = ("ing", "ies", "ied", "ed", "es", "s")


def _stem(token: str) -> str:
    """A prefix that matches the word however the archive inflected it.

    Never shorter than four characters, because a three-letter prefix matches
    most of the vocabulary and the regex below is anchored at a word start, not
    a whole word.
    """
    if token.endswith("nt") and token[:-2] in _CONTRACTION_STEMS:
        return token[:-1]           # doesnt -> doesn, which matches "Doesn't"
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[:-len(suf)]
    return token


def _tag_core_words(value: str) -> list[str]:
    core = value
    for rx in _AO3_WRAPPER_RES:
        core = rx.sub(" ", core)
    # \w, not [A-Za-z0-9]: `Coffee Shops & Cafés` split on ASCII letters gives
    # ["coffee", "shops", "caf", "s"], and that phantom fourth word dropped the
    # 20,078-work spelling of the coffee-shop trope below the coverage line.
    return [w for w in re.split(r"[^\w']+", core.lower(), flags=re.UNICODE) if w]


def _tag_coverage(value: str, window: tuple[str, ...]) -> float:
    """What fraction of this tag the reader actually named. 0.0 if any of their
    words survives only in the structural furniture."""
    words = _tag_core_words(value)
    if not words:
        return 0.0
    for token in window:
        stem = _stem(token)
        if not any(w.startswith(stem) for w in words):
            return 0.0
    content = [w for w in words if w not in _STOPWORDS] or words
    return len(window) / len(content)


def _lookup_window(db, window: tuple[str, ...]) -> list[tuple[str, int]]:
    """Tags containing every word of `window`, most-used first. Cached per
    process for _TROPE_TTL — the same tropes are searched over and over."""
    hit = _TROPE_SQL_CACHE.get(window)
    if hit and time.monotonic() - hit[1] < _TROPE_TTL:
        return hit[0]
    clauses = " AND ".join(f"value ~* :p{i}" for i in range(len(window)))
    params = {f"p{i}": r"\m" + re.escape(_stem(t)) for i, t in enumerate(window)}
    params.update(min_works=TROPE_TAG_MIN_WORKS, max_len=_TROPE_TAG_MAX_LEN,
                  cap=_TROPE_SQL_LIMIT)
    try:
        rows = [(r[0], r[1]) for r in db.execute(
            sql_text(_TROPE_SQL_TMPL.format(clauses=clauses)), params).fetchall()]
    except Exception:
        # The request's own session, and search() has not run its real query
        # yet — the same reason _alias_table and _pair_lookup roll back here.
        # Without it the next statement raises InFailedSqlTransaction and the
        # whole search 500s, naming neither this function nor the real fault.
        db.rollback()
        return []
    if len(_TROPE_SQL_CACHE) > 4096:
        _TROPE_SQL_CACHE.clear()
    _TROPE_SQL_CACHE[window] = (rows, time.monotonic())
    return rows


def resolve_trope_tags(db, text: str,
                       variants: list[str] | None = None
                       ) -> tuple[list[str], int, str, bool]:
    """The trope a phrase names, as (tag spellings, works, leftover text, whole).

    The lookup is a conjunction of substring matches over `facets`, served by
    `ix_facets_kind_value_trgm`, so word ORDER does not matter — which is the
    whole point. "harry slytherin" and "slytherin harry" are the same request
    and the archive files both under `Slytherin Harry Potter`; before this, one
    of them ranked the 2,450 works actually tagged with it and the other ranked
    works with the two words in their title.

    Measured cold on the live vocabulary (1.57M tag rows): 40ms, and 11-22ms
    warm.

    `variants` are the same query in the archive's own words (see
    `_alias_expand`). They are tried FIRST and in order, because the reader's
    coined word is by definition not in the vocabulary — "wandcrafter" appears
    in no tag and "wandmaker" is on 37 works.

    WINDOWS, longest first. A reader names a trope and then narrows it —
    "time travel naruto", "soulmate au bts" — and no single tag holds both
    halves. So the longest contiguous run of the reader's words that IS a tag
    wins, and the words left over are returned to be AND-ed onto the tag branch
    by the caller. That is what keeps a resolved trope from leaking across
    fandoms: `Time Travel` is 45,960 works and "time travel naruto" must not
    return the 44,000 of them that are not Naruto.

    `whole` says the tag consumed every content word. Only then may the caller
    treat the query as a CATEGORY for ranking — a partial match is evidence
    that a branch is worth OR-ing, not evidence about what the reader meant by
    the whole query.

    A one-word window is admitted only with leftover words to bound it (or when
    an alias produced it). Unbounded, it would fire on any one-word query —
    "harry" matches thousands of tags the text search already matches on its
    own, so it would widen the search enormously and teach the ranker nothing.
    """
    if not TROPE_TAGS_ON:
        return [], 0, "", False
    # The reader named a fandom, a ship or a character. Nothing to resolve.
    if _names_a_thing(db, " ".join(text.lower().split())):
        return [], 0, "", False

    aliased = bool(variants)
    for candidate in (variants or []) + [text]:
        tokens = _trope_tokens(candidate)
        if not tokens or len(tokens) > 6:
            continue
        found = _windows(db, candidate, tokens, aliased)
        if found:
            return found
    return [], 0, "", False


def _windows(db, text: str, tokens: list[str], aliased: bool):
    """The window search over one phrasing. See resolve_trope_tags."""
    asked_negative = bool(_NEGATION_RE.search(text))
    for n in range(min(len(tokens), 4), 0, -1):
        for start in range(0, len(tokens) - n + 1):
            window = tuple(tokens[start:start + n])
            leftover = tokens[:start] + tokens[start + n:]
            if n < 2 and not (leftover or aliased):
                continue
            if n < 2 and len(window[0]) < 5:
                continue
            # A window that NAMES something is not a trope. "long haikyuu fics"
            # resolved to `Haikyuu - Freeform`, which is the fandom wearing a
            # tag's clothes, and "fics where sasuke defects" to `Birth Defects`
            # once "sasuke" was recognised as a character and dropped out.
            if _names_a_thing(db, " ".join(window)):
                continue
            kept = [(v, c) for v, c in _lookup_window(db, window)
                    if not _REFERENCE_TAG_RE.match(v)
                    and (asked_negative or not _NEGATION_RE.search(v))
                    and _tag_coverage(v, window) >= TROPE_TAG_MIN_COVERAGE
                    and (n > 1 or c >= TROPE_TAG_MIN_WORKS_SOLO)
                    ][:TROPE_TAG_CAP]
            if not kept:
                continue
            # A trope that accounts for only PART of the query still says what
            # the reader meant by the whole of it, PROVIDED the rest names a
            # thing. "omegaverse bakugou" is a trope and a character and
            # nothing else, so it is a category query — but `_query_is_category`
            # cannot see that: it probes exact sub-phrases and deliberately
            # excludes single words, so a one-word trope beside one character
            # scored zero and the query fell to the title weights. Works with
            # the shorthand in their title and no readers came first.
            whole = not leftover or all(_names_a_thing(db, w) for w in leftover)
            return ([v for v, _ in kept], max(c for _, c in kept),
                    " ".join(leftover), whole)
    return None


def resolve_intent(db, raw: str) -> Intent:
    """Everything above, in order. `db` is the request's own session."""
    if not QUERY_INTENT_ON:
        return Intent(text=raw)
    intent = read_request(raw)
    if intent.text:
        intent.text_variants, intent.is_shorthand = _alias_expand(intent.text)
        (intent.tags, intent.tag_works,
         intent.tag_leftover, intent.tag_is_whole) = resolve_trope_tags(
            db, intent.text, intent.text_variants)
        intent.tag_branch_ok = bool(intent.tags) and (
            bool(intent.tag_leftover)
            or intent.tag_works <= TROPE_BRANCH_MAX_WORKS)
    return intent
