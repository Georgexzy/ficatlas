"""Detect author opt-out notices in story summaries.

Some authors state in a work's summary that they do not want their work
reposted, redistributed, or placed on external sites. FicAtlas indexes
third-party works, so honouring that is an explicit choice: such works are
skipped at ingest and removed from the index.

Conservative by design — removing a work is destructive, so a summary is only
treated as an opt-out when the refusal is unambiguous:

  * Tier 1 — verbs that inherently mean moving the work to another surface
    (repost / re-publish / redistribute / re-upload) preceded by a negative
    directive ("do not", "don't", "please don't", "you do NOT"). The verb
    carries the intent itself: "do not repost" needs no qualifier.
  * Tier 2 — put-on-a-site verbs (post / upload / copy / steal / put / share /
    feed / print / bind) preceded by a negative directive AND naming an
    external surface ("another site", "elsewhere", Goodreads, ...). The surface
    is required so "don't post mean reviews" or "I posted this on another site
    too" never match.

Never matched: grant language such as "Licensed to translate and redistribute",
which is permission to repost, not a refusal of it.
"""

import re

_NEG = (
    r"(?:do\s+not|don'?t|dont|please\s+do\s+not|please\s+don'?t|"
    r"you\s+(?:do\s+)?not)"
)

# "do not have permission to repost" — the verb trails a noun phrase, not the
# directive directly.
_PERMISSION = (
    r"(?:do\s+not\s+have\s+permission\s+to|have\s+no\s+permission\s+to|"
    r"no\s+permission\s+to)"
)

# Moving the work to another surface: the refusal is carried by the verb.
_TIER1_VERB = (
    r"(?:re[- ]?post|re[- ]?publish|re[- ]?distribut(?:e|ion|ing)?|"
    r"redistribut(?:e|ion|ing)?|re[- ]?upload)"
)

# Placing the work on a site: only a refusal when an external surface is named.
_TIER2_VERB = r"(?:post|upload|copy|steal|put|share|feed|print|bind)"

_SURFACE = (
    r"\b(?:other\s+sites?|other\s+websites?|another\s+site|any\s+other\s+site|"
    r"elsewhere|other\s+platforms?|another\s+platform|other\s+archives?|"
    r"goodreads|storygraph|another\s+website|other\s+social\s+media)\b"
)

# `[^.!?]{0,120}` keeps Tier 2 from reaching into a later sentence to grab a
# stray "other site" that belongs to a different, non-refusal clause.
_TIER1 = re.compile(rf"{_NEG}\s+{_TIER1_VERB}\b", re.IGNORECASE)
_TIER1_PERMISSION = re.compile(rf"\b{_PERMISSION}\s+{_TIER1_VERB}\b", re.IGNORECASE)

# Tier 2 must not match "post reviews/comments on other sites" — that tells
# READERS not to post reviews, which is the opposite of a no-repost notice. The
# negative lookahead after the verb refuses a review/comment/note object.
_TIER2 = re.compile(
    rf"{_NEG}\s+{_TIER2_VERB}\b"
    rf"(?!.{0,30}?\b(?:reviews?|comments?|notes?|a\s+review)\b)"
    rf"[^.!?]{{0,120}}?{_SURFACE}",
    re.IGNORECASE,
)


def has_external_optout(summary: str | None) -> bool:
    """True when `summary` contains an explicit, unambiguous no-external-sites
    notice from the author (the work should not be indexed)."""
    return match_external_optout(summary) is not None


def match_external_optout(summary: str | None) -> str | None:
    """The matched opt-out snippet, for human review of a dry run. `None` when
    the summary carries no opt-out."""
    if not summary:
        return None
    for rx in (_TIER1, _TIER1_PERMISSION, _TIER2):
        m = rx.search(summary)
        if m:
            start = max(0, m.start() - 30)
            end = min(len(summary), m.end() + 30)
            return "…" + summary[start:end].replace("\n", " ") + "…"
    return None
