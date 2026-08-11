"""Author names that are hostile to SQL.

The index does not hold tidy author names. It holds whatever the scrapers found
where an author name should have been, and some of that is page furniture: a
Blogger share widget is stored in this index as the author

    "Get link \\n Facebook \\n Twitter \\n Pinterest \\n Email \\n Other Apps"

That value used to abort the entire series pass with

    syntax error at or near "twitter"

and the mechanism is worth remembering, because nothing about the calling code
looked wrong. The query was fully parameterised. But it also carried a long
`--` comment EXPLAINING the parameter, which mentioned `:a` seven times.
SQLAlchemy's text() binds every `:a` it finds anywhere in the string, comments
included, and psycopg2 substitutes the value at each one — so an author name
containing a newline had its first line stay inside the comment while the rest
escaped onto their own lines as bare SQL.

Three authors in nearly twenty million rows could do this, and the cost was not
three lost authors: the worker loop only advances its cursor on success, so the
pass retried the same doomed batch every three hours and series detection was
stalled indefinitely.

So the rule these tests defend is: parameter tokens do not go in SQL comments.
"""
import os
import sys

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The real value from the live index, kept verbatim.
WIDGET_AUTHOR = "Get link \n Facebook \n Twitter \n Pinterest \n Email \n Other Apps"

HOSTILE = [
    pytest.param(WIDGET_AUTHOR, id="blogger-share-widget"),
    pytest.param("Link abrufen \n Facebook \n Twitter \n Andere Apps", id="german-widget"),
    pytest.param("O'Brien", id="apostrophe"),
    pytest.param("me--too", id="looks-like-a-sql-comment"),
    pytest.param("a\nDROP TABLE stories", id="newline-then-sql"),
    pytest.param("50% of the time", id="percent-sign"),
]


class TestTheAuthorLookupSurvives:
    """The exact query series_detect runs per author."""

    @pytest.mark.parametrize("author", HOSTILE)
    def test_it_does_not_raise(self, db, author):
        rows = db.execute(text("""
            SELECT id, title FROM stories
             WHERE lower(author) = :a AND title IS NOT NULL AND delisted_at IS NULL
        """), {"a": author.lower()}).fetchall()
        assert isinstance(rows, list)

    def test_it_finds_the_story_it_should(self, db):
        """Not raising is not enough — a value that round-trips to nothing would
        pass the test above while quietly matching no work at all."""
        db.execute(text("""
            INSERT INTO stories (site, site_id, url, title, author)
            VALUES ('ao3', 'widget1', 'https://example.test/widget1', 'A Work', :a)
        """), {"a": WIDGET_AUTHOR})
        db.commit()
        rows = db.execute(text(
            "SELECT title FROM stories WHERE lower(author) = :a"
        ), {"a": WIDGET_AUTHOR.lower()}).fetchall()
        assert [r[0] for r in rows] == ["A Work"]

    def test_a_newline_author_cannot_execute_injected_sql(self, db):
        """The failure mode was a value escaping its comment into executable
        position. Bound properly it is inert, and `stories` is still there."""
        db.execute(text("SELECT 1 FROM stories WHERE lower(author) = :a"),
                   {"a": "x\n; DROP TABLE stories; --"}).fetchall()
        db.commit()
        assert db.execute(text(
            "SELECT count(*) FROM pg_class WHERE relname = 'stories'"
        )).scalar() == 1


class TestTheSourceItself:
    def test_no_sql_comment_mentions_a_bind_parameter(self):
        """The actual defect, checked at its source rather than through its
        symptom — a future comment reintroducing `:param` would compile, run, and
        only fail against data nobody has locally."""
        import pathlib
        import re

        backend = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in backend.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                s = line.strip()
                if s.startswith("--") and re.search(r"(?<![:\w]):[a-zA-Z_]\w*", s):
                    offenders.append(f"{path.name}:{n}: {s[:70]}")
        assert not offenders, "bind tokens inside SQL comments:\n" + "\n".join(offenders)
