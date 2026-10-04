"""Startup must not lock `stories` to discover there is nothing to do.

ADD COLUMN IF NOT EXISTS and CREATE INDEX IF NOT EXISTS take their table lock
BEFORE checking, and 51 of the startup statements touch `stories`. Measured
2026-10-04: a restart during the popularity pass queued reads and writes behind
a no-op ALTER, statement after statement. Decided from the catalogue instead.
"""
from init_db import already_applied

COLS = {("stories", "cross_post_urls"), ("stories", "delisted_reason")}
IDX = {"ix_stories_title_lower"}


def test_an_existing_column_is_not_re_added():
    assert already_applied("ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[] DEFAULT '{}'", COLS, IDX)


def test_a_new_column_still_runs():
    assert not already_applied("ALTER TABLE stories ADD COLUMN IF NOT EXISTS brand_new INT", COLS, IDX)


def test_mixed_clauses_still_run():
    stmt = ("ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[], "
            "ALTER COLUMN title SET NOT NULL")
    assert not already_applied(stmt, COLS, IDX)


def test_every_added_column_must_exist():
    stmt = ("ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[], "
            "ADD COLUMN IF NOT EXISTS other INT")
    assert not already_applied(stmt, COLS, IDX)
    assert already_applied(stmt.replace("other", "delisted_reason"), COLS, IDX)


def test_an_existing_index_is_not_rebuilt():
    assert already_applied("CREATE INDEX IF NOT EXISTS ix_stories_title_lower ON stories (lower(title))", COLS, IDX)
    assert not already_applied("CREATE INDEX IF NOT EXISTS ix_new ON stories (x)", COLS, IDX)


def test_other_statements_always_run():
    assert not already_applied("CREATE TABLE IF NOT EXISTS t (a int)", COLS, IDX)
    assert not already_applied("CREATE OR REPLACE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql", COLS, IDX)
