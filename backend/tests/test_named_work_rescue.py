"""When a reader NAMES a work and the search hears nothing.

Both shapes come from real zero-result searches in the traffic log. They are
not spelling guesses: the reader has identified one work, and the search simply
had no way to parse how they said it.
"""
import uuid

import pytest
from sqlalchemy import text

import api.search as S

pytestmark = pytest.mark.usefixtures("db")


def _work(db, title, author, site_id=None, kudos=0):
    sid = uuid.uuid4()
    db.execute(text("""
        INSERT INTO stories (id, site, site_id, url, title, author, kudos)
        VALUES (:i,'ao3',:s,:u,:t,:a,:k)
    """), {"i": str(sid), "s": site_id or str(uuid.uuid4())[:12],
           "u": f"https://archiveofourown.org/works/{sid}",
           "t": title, "a": author, "k": kudos})
    db.commit()
    return str(sid)


class TestTitleByAuthor:
    """"<Title> by <Author>" is *the* way fanfiction is named — it is how every
    recommendation thread writes one. The words "by" and the author's name were
    ANDed against the index as ordinary text, which no work contains, so the
    commonest way to name a work returned nothing."""

    def test_identifies_the_work_when_the_author_matches(self, db):
        _work(db, "Manacled", "SenLinYu")
        out = S._names_one_work(db, "Manacled by SenLinYu")
        assert [s.kind for s in out] == ["work"]
        assert 'author:"SenLinYu"' in out[0].query

    def test_the_author_is_what_disambiguates(self, db):
        """The index holds five works called "Manacled"."""
        _work(db, "Manacled", "SenLinYu", kudos=900)
        _work(db, "Manacled", "Somebody Else", kudos=10)
        out = S._names_one_work(db, "Manacled by Somebody Else")
        assert out[0].kind == "work"
        assert "Somebody Else" in out[0].query

    def test_falls_back_to_the_title_when_the_author_is_unknown(self, db):
        """Taken from the real search `Battlefields by Aislin Avalban`: several
        works are called Battlefields and none is by that author. The title
        alone is still the better search, and is offered AS the title rather
        than pretending the work was identified."""
        _work(db, "Battlefields", "Ellepige")
        _work(db, "Battlefields", "spaceconspiracy")
        out = S._names_one_work(db, "Battlefields by Aislin Avalban")
        assert out and out[0].kind == "text"
        assert out[0].value == "Battlefields"
        assert "author" not in out[0].query

    def test_says_nothing_about_a_title_that_is_not_here(self, db):
        assert S._names_one_work(db, "No Such Work by No Such Person") == []

    def test_an_ordinary_query_containing_by_is_untouched(self, db):
        # "Stand by Me", "Side by Side" — a rescue that fires on these would be
        # worse than no rescue, and this one only fires on an EXACT title match.
        _work(db, "Stand by Me", "Someone")
        assert S._names_one_work(db, "harry potter") == []


class TestABareArchiveId:
    def test_finds_a_work_pasted_as_a_bare_id(self, db):
        _work(db, "Some Long Fic", "An Author", site_id="11834427")
        out = S._names_one_work(db, "11834427")
        assert out and out[0].kind == "work"
        assert out[0].value == "Some Long Fic"

    def test_says_nothing_when_the_id_is_not_indexed(self, db):
        """The honest answer. Inventing a suggestion for a work this index does
        not hold spends the reader's last patience on a dead end — the same
        rule reddit_recs_import follows for unmatched links."""
        assert S._names_one_work(db, "11834427") == []

    def test_short_numbers_are_left_alone(self, db):
        """"1984" and "Chapter 100" are titles, not ids."""
        assert S._names_one_work(db, "1984") == []
        assert S._names_one_work(db, "100") == []
