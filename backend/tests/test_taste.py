"""What a reader's "I have already read" list says about what they want.

Fic-finder posts almost always carry one, and it is the richest signal in the
request — far better than the adjectives around it. It was being thrown away.
"""
import pytest
from sqlalchemy import text

from api.search import taste


@pytest.fixture()
def works(db):
    db.execute(text("DELETE FROM stories"))
    db.execute(text("DELETE FROM facets"))
    # Three drarry slow-burns and one unrelated work, plus the provenance tags
    # every real row carries.
    rows = [
        ("Evitative",      ["Slow Burn", "Romance", "ao3_meta_dump"], ["Draco Malfoy/Harry Potter"], 93625),
        ("Running on Air", ["Slow Burn", "Romance", "ao3_meta_dump"], ["Draco Malfoy/Harry Potter"], 72775),
        ("Written on the Heart", ["Slow Burn", "ao3_meta_dump"],      ["Draco Malfoy/Harry Potter"], 42140),
        ("Some Other Fic", ["Crack", "ffnet_dump"],                   [],                            10),
    ]
    for title, tags, rels, kudos in rows:
        db.execute(text("""
            INSERT INTO stories (id, site, site_id, title, url, tags, relationships,
                                 kudos, language, status, word_count, chapter_count)
            VALUES (gen_random_uuid(), 'ao3', :sid, :t, :u, CAST(:tags AS text[]),
                    CAST(:rels AS text[]), :k, 'en', 'complete', 1000, 1)
        """), {"sid": title[:20], "t": title, "u": "http://x/" + title[:8],
               "tags": tags, "rels": rels, "k": kudos})
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Slow Burn',193882), ('tag','Romance',374075),
          ('relationship','Draco Malfoy/Harry Potter',47460)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM stories"))
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_it_derives_what_their_works_have_in_common(works):
    out = taste(titles="Evitative|Running on Air|Written on the Heart", db=works)
    values = [t.value for t in out.tags]
    assert "Draco Malfoy/Harry Potter" in values
    assert "Slow Burn" in values
    assert out.query, "a runnable query, not just a list"


def test_provenance_tags_are_not_taste(works):
    """`ao3_meta_dump` and `ffnet_dump` record which IMPORT a row came from and
    live in the same array as real tags. Left in, they dominated completely —
    four well-known works shared nothing but the script that imported them, and
    the derived "taste" was that fact."""
    out = taste(titles="Evitative|Running on Air|Written on the Heart", db=works)
    assert not any(t.value.endswith("_dump") for t in out.tags)


def test_a_tag_on_only_one_work_is_description_not_taste(works):
    """One work's tags describe that work. The signal is what its neighbours
    have in common, so a tag needs at least two of them."""
    out = taste(titles="Evitative|Some Other Fic", db=works)
    assert "Crack" not in [t.value for t in out.tags]


def test_titles_it_cannot_find_are_reported_not_swallowed(works):
    """The index holds 20.5M works and not everything. A title that is not
    there has to be visible, or the derived taste is quietly built from half
    the list."""
    out = taste(titles="Evitative|Daft Morons", db=works)
    assert "Daft Morons" in out.unmatched
    assert [m.title for m in out.matched] == ["Evitative"]


def test_nothing_named_yields_nothing_rather_than_failing(works):
    out = taste(titles="   ", db=works)
    assert out.matched == [] and out.tags == [] and out.query == ""
