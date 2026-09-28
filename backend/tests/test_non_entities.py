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
