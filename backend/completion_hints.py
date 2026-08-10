"""Reading completion out of a summary, for archives that have no flag for it.

FanFiction.net has no completion field the way AO3 does, so authors have always
written it into the summary by hand: "COMPLETE!", "[Oneshot: COMPLETE]",
"NOW COMPLETE", "**COMPLETE**". 53,240 FF.net works in this index said so and
were still being shown to readers as status unknown.

The whole difficulty is that "complete" is also an ordinary English word, and in
fandom it is a very common adjective: "complete AU", "complete rewrite",
"complete disaster", "complete crack". Matching it loosely would mark tens of
thousands of unfinished works as finished, which is the failure that actually
annoys a reader — starting something advertised as done and finding it abandoned
mid-scene.

Two things keep it honest:

  * ALL CAPS is required. Authors write the status marker in caps and the
    adjective in lower case, and that single constraint removes almost all of
    the ambiguity by itself.
  * An explicit reject list for the phrases where caps would still mislead —
    negations ("NOT COMPLETE"), futures ("WILL BE COMPLETE") and the adjective
    uses that do sometimes get shouted ("COMPLETE AU").

Sampled before it was applied: of sixteen random matches, every one was an
unambiguous status marker. Applied only where status is already `unknown` — it
fills in what we do not know and never overrides what an archive has told us.
"""
from __future__ import annotations

import re

# The marker itself: caps only. Lower-case "complete" is the adjective far more
# often than the status, and there is no need to guess when authors are this
# consistent about shouting it.
_MARKER = re.compile(r"(?<![A-Za-z])(COMPLETE|COMPLETED)(?![A-Za-z])")

# Cases where the word is present but does not mean "this work is finished".
_REJECT = re.compile(
    r"in[-\s]?complete"
    r"|(?:not|isn'?t|never|far from|nearly|almost|nowhere near|will be|to be|soon)"
    r"\s+(?:be\s+)?complete"
    r"|complete[d]?\s+(?:au|a\.u\.|rewrite|overhaul|crack|mess|disaster|guide"
    r"|collection|nonsense|fluff|garbage|trash|idiot|failure|stranger|opposite"
    r"|contrast|change|list|set|works)",
    re.IGNORECASE,
)


def declares_complete(summary: str | None) -> bool:
    """Does this summary say, in the author's own words, that the work is done?

    Conservative by design: a false negative leaves a finished work marked
    unknown, which is what it already was. A false positive tells a reader an
    abandoned work is finished, which is the failure worth avoiding.
    """
    if not summary:
        return False
    if _REJECT.search(summary):
        return False
    return bool(_MARKER.search(summary))
