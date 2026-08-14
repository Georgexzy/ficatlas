"""Pytest fixtures for DB-backed integration tests.

These must NEVER run against the production index. A DB fixture only exists when
TEST_DATABASE_URL points at a database whose name ends in `_test`; otherwise the
test is skipped. The name check is the guardrail — pointing this at a real
database would let a test create and delete rows in the live 19.7M-row table.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# All app tables that the ingest/import path can touch, so each test starts from
# a clean slate. CASCADE also clears dependent rows (chapters, grants, takedowns).
_TRUNCATE = (
    "stories", "chapters", "series", "series_works", "facets",
    "users", "user_sessions", "user_hosted", "takedowns", "source_gone",
    "search_cache_entries", "visit_events",
)

_engine = None
_Session = None


def _apply_schema(engine):
    """Bring the test database up to the production DDL.

    Without this the test schema is whatever someone created by hand once, and it
    drifts silently every time a table is added to init_db.py. That drift is not
    loud: the shared search cache was added, its tests ran green against a
    database with no such table, and they passed only because a missing table is
    deliberately indistinguishable from a cache miss. Tests that cannot fail are
    worse than no tests, so the schema comes from the same source the real
    database does.

    Statement failures are tolerated INDIVIDUALLY, each in its own transaction.
    Some of init_db.py's statements are maintenance rather than structure —
    planner statistics targets, backfills — and are meaningless against an empty
    database; one of them raising must not stop the tables after it from being
    created. Anything genuinely missing still fails, loudly, in the test that
    needs it.
    """
    import init_db

    for stmt in init_db._split_statements(init_db.SQL):
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass


def _build():
    global _engine, _Session
    if _engine is not None:
        return _engine, _Session
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    dbname = url.rsplit("/", 1)[-1]
    if not dbname.endswith("_test"):
        _engine, _Session = None, None
        return None, None
    _engine = create_engine(url)
    _apply_schema(_engine)
    _Session = sessionmaker(bind=_engine)
    return _engine, _Session


@pytest.fixture()
def db():
    """A session to the test database, truncated before each test."""
    engine, Session = _build()
    if engine is None:
        pytest.skip("set TEST_DATABASE_URL to a *_test database for DB tests")
    session = Session()
    try:
        session.execute(text(f"TRUNCATE {', '.join(_TRUNCATE)} RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()
