"""How much each missing field is worth fixing. One scale, used by both sides.

There were two, and they disagreed about the most important thing.

`gap_filler.py` ranks the crawler's queue — which row to spend a request on
next — and weighted `summary` 5 against `characters` 2. The admin panel ranks
what is wrong with the index, and weighted characters/pairings highest and
summary lowest. So the panel said the biggest problem was six and a half
million FanFiction.net works nobody can find by pairing, while the job that
exists to close gaps was spending its requests on blurbs.

Neither was wrong about its own question; they were answering different ones
and nobody had reconciled them. This is the reconciliation, and both import it,
so "the system works on what the panel says matters" is true by construction
rather than by coincidence.

THE ORDER, and the argument for it:

  A work nobody can FIND is worse than a work that looks plain.

  Characters and relationships are how readers filter — they are the two most
  used filters on the site, and a work missing them is invisible to both, no
  matter how good its blurb is. A missing summary makes a result look broken in
  a list, which is real and is why it ranks next; but the reader is at least
  looking at it.

  Length and date are filters and sorts: they narrow a result set rather than
  decide whether a work can appear in one.

  Engagement and language come last. Engagement only moves a work within an
  ordering it already appears in, and language matters to a filter most readers
  never touch.
"""
from __future__ import annotations

# Higher fixes first. The absolute numbers carry no meaning beyond their order
# and their ratios; they are kept small so a gap score stays readable in a log.
FIELD_PRIORITY: dict[str, int] = {
    "characters":   5,
    "relationships": 5,
    "summary":      4,
    "word_count":   3,
    "published_at": 3,
    "updated_at":   2,
    "genres":       1,
    "kudos":        1,
    "language":     1,
}

# What a reader loses when the field is missing, for a panel a person reads.
# Written as the consequence rather than the field name, because "no_chars"
# says nothing about why anybody should care.
FIELD_COST: dict[str, str] = {
    "characters":   "cannot be found by character",
    "relationships": "cannot be found by pairing",
    "summary":      "shows no blurb on a result card",
    "word_count":   "cannot be filtered by length",
    "published_at": "cannot be sorted by date",
    "updated_at":   "cannot be sorted by activity",
    "genres":       "cannot be filtered by genre",
    "kudos":        "sorts behind every scored work",
    "language":     "cannot be filtered by language",
}

# The admin panel counts the same things under its own column names.
COVERAGE_FIELD = {
    "no_chars":   "characters",
    "no_ships":   "relationships",
    "no_summary": "summary",
    "no_words":   "word_count",
    "no_date":    "published_at",
    "no_genres":  "genres",
    "no_kudos":   "kudos",
}
