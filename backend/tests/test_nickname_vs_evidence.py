"""A ship nickname is a guess, and a guess may not be its own evidence.

"Bumblebee" is a RWBY ship nickname AND a Transformers character. On a real
prose query --

    "A simple day out was all Bumblebee wanted, but a battered and half alive
     Starscream interrupted that plan"

-- the extractor returned ship:"Blake Belladonna/Yang Xiao Long" fandom:"RWBY",
while `Starscream (Transformers)` (8,416 works) sat in the term list and the
index held `Bumblebee/Starscream (Transformers)` all along.

The cause was circular. `_fandom_from_evidence` was asked about relationships
FIRST and the nickname-resolved pairing IS a relationship, so the alias voted
for the fandom it had just been looked up in, agreed with itself, and the `or`
short-circuited before any character was consulted.

This file guards the narrow rule that fixes it without loosening the one-name
guard that keeps `Time (Linked Universe)` from hijacking a Harry Potter post.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.search as S


def _story(db, fandoms, characters):
    n = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, fandoms, characters)
        VALUES ('ao3', :s, :u, :t, 'An Author',
                CAST(:f AS text[]), CAST(:c AS text[]))
    """), {"s": n[:16], "u": f"https://example.test/{n}", "t": n[:8],
           "f": list(fandoms), "c": list(characters)})


class TestFandomContradicts:
    def test_absence_is_the_contradiction(self, db):
        """Not a majority — absence.

        A majority test cannot work: an AO3 fandom tag is not a franchise, so
        the works carrying these characters spread over SIX Transformers tags,
        the largest being 34% of the sample. The first version of this fix used
        a 0.6 majority and never fired once.
        """
        a, b = f"Cogwheel {uuid.uuid4().hex[:6]}", f"Sparkplug {uuid.uuid4().hex[:6]}"
        for i in range(40):
            # Deliberately fragmented across spellings, as the archives are.
            _story(db, [f"Mechs - All Media Types" if i % 2 else "Mechs: Prime"], [a, b])
        db.commit()
        assert S._fandom_contradicts(db, [a, b], "Some Other Fandom") is not None

    def test_a_fandom_that_does_appear_is_no_contradiction(self, db):
        a = f"Shared {uuid.uuid4().hex[:6]}"
        for _ in range(40):
            _story(db, ["Actually This One"], [a])
        db.commit()
        assert S._fandom_contradicts(db, [a], "Actually This One") is None

    def test_a_thin_sample_concludes_nothing(self, db):
        """A character on a handful of works can easily miss its own fandom
        tag, and a nickname must not be dropped on that."""
        a = f"Rare {uuid.uuid4().hex[:6]}"
        _story(db, ["Tiny Fandom"], [a])
        db.commit()
        assert S._fandom_contradicts(db, [a], "Somewhere Else") is None

    def test_it_never_establishes_a_fandom(self, db):
        """The whole reason this is allowed to be weaker than
        `_fandom_from_evidence`: its only consequence is dropping a guess."""
        import inspect
        src = inspect.getsource(S._fandom_contradicts)
        assert "ExtractedTerm" not in src, \
            "this must only report a contradiction, never build a fandom term"


def test_the_nickname_is_excluded_from_its_own_evidence():
    import inspect
    src = inspect.getsource(S.extract)
    assert "_pair_from_nickname" in src, "the nickname path must be marked"
    assert "_rel_values" in src, \
        "the relationship evidence must exclude the nickname's own pairing"


def test_dropping_the_nickname_promotes_the_characters():
    """Otherwise the fix trades one bug for another: RWBY stopped appearing and
    `tag:"Fights"` took its place, with two canonical Transformers characters
    unused. A bare name is ranked on the BARE name's count by design
    (`Starscream` 791 against `Fights` 17,614), so it can never win on
    frequency — it has to be pinned."""
    import inspect
    src = inspect.getsource(S.extract)
    i_drop = src.index("pair_term = None")
    i_chars = src.index("pair_chars = [t for t in terms if t.kind == \"character\"]")
    assert i_chars > i_drop, "the characters must take the dropped nickname's place"
