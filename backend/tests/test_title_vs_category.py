"""A title is not a trope, and a full page can still be missing the one work.

All three rules here come from one reader in the traffic log who searched
`a breach in hell` four times and opened nothing. `SCP - A Breach in Hell` is
in the index -- 53,101 words, not delisted, not gated -- and they never saw it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.search as S


def _facet(db, kind, value, count):
    """Seed one vocabulary row. conftest truncates the app tables, so these
    counts have to be stated rather than read from the live index."""
    from sqlalchemy import text
    db.execute(text("INSERT INTO facets (kind, value, count) VALUES (:k,:v,:c) "
                    "ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count"),
               {"k": kind, "v": value, "c": count})
    db.commit()


class TestCategoryFloor:
    """A multi-word phrase on sixteen works is not a category.

    Being called one drops `w_exact` from 4.0 to 0.15 -- a 26x cut to the
    exact-title bonus -- so a title colliding with a tiny tag stops being
    rankable as a title. The docstring on `_query_is_category` already said a
    13-work tag means "a title"; the code returned 13, which every caller
    reads as true.
    """

    def test_a_multi_word_phrase_needs_real_use(self, db):
        # 16, 13, 12 and 26 works were the observed false positives.
        _facet(db, "tag", "a breach in hell", 16)
        _facet(db, "tag", "all the young dudes", 13)
        assert S._query_is_category(db, "a breach in hell") == 0
        assert S._query_is_category(db, "all the young dudes") == 0

    def test_a_real_multi_word_category_survives(self, db):
        # Every real trope measured sat at 2,768 or above.
        _facet(db, "tag", "coffee shop au", 2768)
        assert S._query_is_category(db, "coffee shop au") >= S._CATEGORY_MIN_WORKS

    def test_one_word_nicknames_are_exempt(self, db):
        """A FLAT FLOOR WAS MEASURED AND REJECTED. Real coined ship nicknames
        sit in the same range as the false positives -- `bellamione` 10,
        `linny` 4, `pansmione` 14 against titles at 12, 13, 16, 26 -- so a
        floor that caught the titles would have silently broken those."""
        _facet(db, "relationship", "bellamione", 10)
        _facet(db, "relationship", "linny", 4)
        assert S._query_is_category(db, "bellamione") > 0
        assert S._query_is_category(db, "linny") > 0

    def test_the_floor_sits_in_the_empty_band(self):
        """Nothing was observed between 26 and 2,768; the floor relies on it."""
        assert 26 < S._CATEGORY_MIN_WORKS < 2768


class TestTitleContainment:
    """Archives prefix titles -- "SCP - ", "[Podfic] ", a series name and a
    colon. Those end with what the reader typed and scored nothing, because
    `exact_bonus` only knew equality and prefix."""

    def test_it_is_gated_on_looking_like_a_title(self):
        import inspect
        src = inspect.getsource(S.search)
        assert "_contain_ok" in src
        assert "_TITLE_CONTAIN_MIN_WORDS" in src, \
            "a one-word query is a trope; %fluff% would lift every title"

    def test_reader_wildcards_are_escaped(self):
        """A query containing `%` would otherwise match every title."""
        import inspect
        src = inspect.getsource(S.search)
        blk = src[src.index("_contain_ok ="):src.index("exact_bonus = case(")]
        assert 'replace("%"' in blk and 'escape=' in blk


class TestTheHiddenNoticeFires:
    def test_it_is_not_limited_to_one_page_of_results(self):
        """It ran only when the whole result set fitted on one page, which is
        right for a two-work search and silent for the 109-result title search
        that actually reaches the log -- 36 works hidden, notice never shown."""
        import inspect
        src = inspect.getsource(S.search)
        assert "_HIDDEN_NOTICE_MAX_RESULTS" in src
        assert "total <= per_page:" not in src, \
            "the page-sized gate is what suppressed the notice"

    def test_it_stays_bounded(self):
        """A reader browsing five thousand results is not missing one specific
        work, and a notice on every search stops being read."""
        assert 25 <= S._HIDDEN_NOTICE_MAX_RESULTS <= 1000
