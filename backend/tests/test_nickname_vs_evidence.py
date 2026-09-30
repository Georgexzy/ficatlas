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


class TestAWeakFandomMayNotEvictStrongCharacters:
    """The same rule, one layer down, and the last piece of the Bumblebee bug.

    Once a post's fandom is known, a character from elsewhere is usually a
    misfire — so `_wrong_fandom` drops it. But the FANDOM is sometimes the
    misfire. Measured on the real prose query:

        "A simple day out was all Bumblebee wanted, but a battered and half
         alive Starscream interrupted that plan"

    `Wanted (2008)`, a fandom on 117 works matched from the ordinary word
    "wanted", evicted `Starscream (Transformers)` (8,416) and
    `Bumblebee (Transformers)` (6,781) — both of which the span loop had
    ranked FIRST and SECOND. The reader got a 117-work film.
    """

    def test_the_check_runs_before_the_filter_it_guards(self):
        """ORDER IS THE WHOLE FIX, and getting it wrong is silent.

        The first version of this sat after `_wrong_fandom` had already
        stripped the characters, so it read an empty character list, never
        fired, and looked exactly like a rule that did not work.
        """
        import inspect
        src = inspect.getsource(S.extract)
        i_words = src.index("_f_words = {w for w in re.findall")
        i_guard = src.index("_FANDOM_OUTWEIGHED")
        i_filter = src.index("if not _wrong_fandom(t.value, t.kind, _f_words)")
        assert i_words < i_guard < i_filter, (
            "the outweigh check must sit between _f_words and the "
            "_wrong_fandom filter, or it reads characters that are already gone")

    def test_it_needs_the_characters_to_be_better_attested(self):
        """Not the contradiction alone — the contradiction holds both ways.

        A named `Harry Potter` (686,826) must keep beating a lone
        `Time (Linked Universe)` (3,888) matched from the word "time", or the
        Zelda bug comes straight back. What separates that from the Bumblebee
        case is which side the numbers are on: 176x one way, 72x the other.
        """
        import inspect
        src = inspect.getsource(S.extract)
        assert "_best_char >= _fand_count * _FANDOM_OUTWEIGHED" in src, \
            "dropping the fandom must be gated on the characters outweighing it"

    def test_the_margin_is_wide(self):
        """Both observed cases are an order of magnitude clear of the
        threshold, so nothing near it has ever actually been seen."""
        assert 1 < S._FANDOM_OUTWEIGHED <= 5


class TestOnlyAGuessMayBeOverruled:
    """The evidence rule, pointed the other way, is the same mistake.

    The overrule exists for ABBREVIATIONS standing in for a fandom -- "OP
    Harry" -> One Piece, "go" -> Good Omens, "re" -> Resident Evil -- where the
    characters should win. A reader who writes the fandom's own NAME has not
    guessed at anything. Measured before this:

        "naruto fic where sasuke and sakura time travel"
          -> fandom:"Fire Emblem: If | Fire Emblem: Fates"   (8,016 works)

    while `Naruto`, matched from the literal word "naruto", is on 456,030. The
    evidence behind it was junk in both halves: the resolved `Sasuke Uchiha` is
    a 237-work spelling and no `Sakura*` character near the top of the
    vocabulary is the Naruto one at all.
    """

    def test_a_named_fandom_is_protected(self):
        import inspect
        src = inspect.getsource(S.extract)
        assert "_named_outright" in src, \
            "the overrule must not fire when the reader named the fandom"
        i_named = src.index("_named_outright = bool(_fmatched)")
        i_use = src.index("and not _named_outright")
        assert i_named < i_use

    def test_an_abbreviation_is_still_overrulable(self):
        """The guard must be EQUALITY, not a prefix: "go" is a prefix of "Good
        Omens" and must stay overrulable, or the hijack this rule exists to
        stop comes back."""
        import inspect
        src = inspect.getsource(S.extract)
        block = src[src.index("_fname = _base(fandom_term.value)"):
                    src.index("and not _named_outright")]
        assert "==" in block, "the name test must be equality"
        assert "startswith" not in block, \
            "a prefix test would protect 'go' -> Good Omens and reopen the hijack"

    def test_the_pipe_split_is_aos_own_convention(self):
        """`僕のヒーローアカデミア | Boku no Hero Academia | My Hero Academia` --
        a reader typing any one of those has named it outright."""
        import inspect
        src = inspect.getsource(S.extract)
        assert '_fname.split("|")' in src
