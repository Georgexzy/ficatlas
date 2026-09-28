"""Words that identify nobody must not become characters.

`_NON_ENTITIES` held "me" and "you" and stopped there, so the words prose is
actually written in went straight through into the vocabulary lookup. Every
pronoun below is a REAL facet value with a real count — found by asking the
vocabulary rather than by thinking of them:

    HE 554   man 184   Girl 116   People 94   Him 91   Woman 88   Boy 88
    Her 78   One 57    them 56    Two 31      they 23   she 23

`HE` alone is on more works than most named characters, so it outranks them
whenever a post contains the word "he" — which is every post.

Found in the traffic log, from a real reader. Somebody pasted a line they
remembered from a fic — "Elena looked at Ethan's way for a second, a hint of
sadness seeing her son" — got nothing, and was offered `char:"Her"`: 78 works
about a character called Her, from the pronoun. They tried four times across two
days and left. With the pronouns suppressed the same sentence now interprets as
`char:"Elena Gilbert"`, which is the character they were describing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import _GENERIC_ENTITIES, _NON_ENTITIES  # noqa: E402


def test_the_pronouns_that_were_missing():
    for w in ("he", "him", "his", "she", "her", "hers",
              "they", "them", "their", "it", "we", "us"):
        assert w in _NON_ENTITIES, w


def test_the_bare_nouns_that_name_nobody():
    for w in ("boy", "girl", "man", "woman", "people", "one", "two"):
        assert w in _NON_ENTITIES, w


def test_what_was_already_there_is_still_there():
    # The set had a job before this and must keep doing it: `None` from "none
    # the wiser", `The Author`, `Main Character`, `Myself`, `Reader`.
    for w in ("none", "the author", "main character", "myself", "reader"):
        assert w in _NON_ENTITIES, w


def test_original_character_is_not_suppressed_as_a_subject():
    """The distinction this file already learned once: an OC identifies no
    FANDOM but is one of the commonest things a fic-finder post asks FOR, so it
    must stay a usable subject. Suppressing it dropped the only thing two posts
    in the corpus were asking for."""
    assert "original character" not in _NON_ENTITIES
    assert "original character" in _GENERIC_ENTITIES


def test_a_real_name_is_never_in_the_set():
    # The failure this set could cause: suppressing somebody real.
    for name in ("harry", "hermione", "draco", "elena", "sirius", "loki"):
        assert name not in _NON_ENTITIES, name


# ── Reader shorthand for a character the archives spell out ─────────────────

def test_the_original_character_abbreviations_are_mapped():
    """`char:"OFC"` asked for a 28-times-smaller set than the reader meant.

    Measured against the vocabulary:

        Original Female Character(s)  218,317      OFC   7,668
        Original Male Character(s)    147,800      OMC   5,385

    So a post saying "Tom Riddle/OFC" searched 7,668 works instead of 218,317
    and threw away 96% of what it was describing. Not derivable —
    _canonical_character matches an exact value or a prefix, and "OFC" is
    neither a prefix nor a substring of the long form — which is why it is
    written out, exactly as _TAG_ABBREV argues for SI and OC.
    """
    from api.search import _CHAR_ALIASES
    assert _CHAR_ALIASES["ofc"] == "Original Female Character(s)"
    assert _CHAR_ALIASES["omc"] == "Original Male Character(s)"
    for k in ("ofcs", "omcs", "oc", "ocs"):
        assert k in _CHAR_ALIASES, k


def test_every_alias_is_lowercased_on_the_left():
    # The lookup is against a lowercased name, so a capitalised key would never
    # be found — silent, and indistinguishable from the alias not existing.
    from api.search import _CHAR_ALIASES
    assert all(k == k.lower() for k in _CHAR_ALIASES)
