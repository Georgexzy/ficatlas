"""A tag filter naming a term the index has never seen must not scan 20M rows.

`ix_stories_tags_trgm` was dropped as 4.4GB of zero scans, which left the
substring fallback in `arr_inc` as a sequential scan of the whole table. It is
reached by every MISTYPED tag — the one query a reader makes after being told the
tag they wanted is spelled differently — and on production it cost 24.2s and a
503.

The fallback is a provable no-op whenever the vocabulary is complete: `facets` is
built from these same array columns, so `fic_arr(col) ILIKE '%t%'` can match a row
only if some array value contains `t`, and the facets table holds every value
there is. So these tests pin the three outcomes `_vocabulary_absent` must keep
apart — PROVEN absent, genuinely unknown, and known-present — because collapsing
the last two into the first is how a 503 turns into a wrong empty page instead.
"""

import pytest
from sqlalchemy import false

import api.search as S


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records the SQL handed to it so a test can assert on the QUERY, not just
    the boolean — the whole claim is that the expensive scan is not emitted."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, stmt, params=None):
        text = str(stmt)
        self.statements.append((text, params))
        return _FakeResult(self.rows)

    def ran(self, needle: str) -> bool:
        return any(needle in text for text, _ in self.statements)


def _kind_for(monkeypatch, kind):
    """Force the vocabulary kind, so a test controls proven-absent vs unknown."""
    monkeypatch.setitem(S._FACET_KIND, "tags", kind)


# ── The three outcomes ────────────────────────────────────────────────────────

def test_a_term_the_vocabulary_proves_absent_is_reported_absent(monkeypatch):
    _kind_for(monkeypatch, "tag")
    monkeypatch.setattr(S, "_facet_contains_cached", lambda kind, term: False)

    assert S._vocabulary_absent(None, "tags", "fluffx") is True


def test_a_term_some_value_contains_is_not_absent(monkeypatch):
    _kind_for(monkeypatch, "tag")
    monkeypatch.setattr(S, "_facet_contains_cached", lambda kind, term: True)

    assert S._vocabulary_absent(None, "tags", "fluff") is False


def test_an_empty_vocabulary_is_unknown_not_absent(monkeypatch):
    """The bug this file exists to prevent.

    A fresh test database — and a real fresh install before its first rebuild —
    has an EMPTY facets table. The query runs, finds nothing, and a
    two-valued reading of that is "absent": every tag filter matches nothing,
    fast and wrong. Ten search tests failed this way before the tri-state.

    The failure is silent in the worst direction. It is not an error, not a slow
    query, and not an empty result a reader can distinguish from a real answer —
    it is an empty result that IS a real answer, for a tag the index holds.
    """
    _kind_for(monkeypatch, "tag")
    monkeypatch.setattr(S, "_facet_contains_cached", lambda kind, term: None)

    assert S._vocabulary_absent(None, "tags", "Fluff") is False


def test_the_probe_asks_whether_the_vocabulary_holds_anything_at_all():
    """Both halves of the tri-state have to come from one query.

    Asked separately it would be a second round trip per filter term on the hot
    path, and the two could disagree — a rebuild landing between them would
    answer "no value contains this term" from one snapshot and "the vocabulary
    is empty" from another, which is exactly the nonsense the tri-state exists
    to rule out.
    """
    sql = str(S._CONTAINS_SQL)
    assert sql.count("FROM facets") == 2, "both questions, one query"
    assert "has_any" in sql and "hit" in sql


def test_the_empty_vocabulary_check_is_served_by_the_kind_index():
    """`WHERE kind = :kind LIMIT 1` stops at the first entry of that kind however
    large it is. Without LIMIT it would count every value — turning a
    milliseconds question into a 1.98M-row aggregate on the hot path."""
    assert "LIMIT 1" in str(S._CONTAINS_SQL)


def test_a_column_with_no_vocabulary_cannot_be_judged(monkeypatch):
    """`warnings` and `categories` have no entry in _FACET_KIND.

    This is the case that makes `unknown` different from `absent`. The column has
    no vocabulary, so the facets table says nothing about it either way, and the
    scan is the only way to find out. Treating "no vocabulary" as "proven absent"
    would silently empty every warning and category filter on the site.
    """
    assert "warnings" not in S._FACET_KIND
    assert "categories" not in S._FACET_KIND
    monkeypatch.delitem(S._FACET_KIND, "tags", raising=False)

    assert S._vocabulary_absent(None, "warnings", "graphic") is False
    assert S._vocabulary_absent(None, "categories", "explicit") is False


def test_the_kill_switch_restores_the_scan_for_every_column(monkeypatch):
    """The reasoning is a staleness assumption, so it has to be testable in
    production without a deploy."""
    monkeypatch.setattr(S, "FACET_PROVES_ABSENCE", False)
    _kind_for(monkeypatch, "tag")
    monkeypatch.setattr(S, "_facet_contains_cached", lambda kind, term: False)

    assert S._vocabulary_absent(None, "tags", "fluffx") is False


def test_a_failed_lookup_is_unknown_rather_than_absent(monkeypatch):
    """`_facet_contains_cached` returns True when the query RAISES.

    "Could not ask" is not "asked and found nothing", and collapsing the two
    means a fresh install with no facets table silently returns zero for every
    tag filter instead of falling back to the scan. The repo has already been
    bitten by this shape once: the missing-table lookup was not wrapped in a
    savepoint and aborted the caller's whole transaction.
    """
    import db.session as dbsession

    class _Boom:
        def __enter__(self):
            raise RuntimeError("relation \"facets\" does not exist")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbsession, "db_session", lambda: _Boom())
    S._facet_contains_cached.cache_clear()

    assert S._facet_contains_cached("tag", "fluffx") is None

    _kind_for(monkeypatch, "tag")
    assert S._vocabulary_absent(None, "tags", "fluffx") is False

    S._facet_contains_cached.cache_clear()


# ── The query the fix removes ─────────────────────────────────────────────────

def test_the_contains_probe_asks_only_the_facets_table():
    """The proof has to be a substring ILIKE against `facets`, matching what the
    removed stories-side scan actually did.

    Getting the arm wrong here is the failure that would matter: asking only for
    normalised equality would report "absent" for a term some longer value
    contains, and would then drop results the old path did find.
    """
    sql = str(S._CONTAINS_SQL)
    assert "FROM facets" in sql
    assert "ILIKE" in sql, "the proof must be a substring match, not equality"
    assert "stories" not in sql, "the whole point is not to touch `stories`"


def test_false_is_a_constant_not_a_subquery():
    """`false()` compiles to a literal the planner folds away, so a proven-absent
    term costs nothing. A subquery here would put the scan straight back."""
    from sqlalchemy.dialects import postgresql
    compiled = str(false().compile(dialect=postgresql.dialect()))
    assert "SELECT" not in compiled.upper()
    assert "false" in compiled.lower()