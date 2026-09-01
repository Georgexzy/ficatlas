"""The fallback DSN, and where the password is actually allowed to come from.

Not a style question. Composing purely from POSTGRES_* looked right and broke
the FictionAlley importer: compose sets POSTGRES_PASSWORD on the `db` service
only, so a process inside the backend or worker container has no POSTGRES_* at
all and got `postgresql://ficatlas@db/...` with no password.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from db import dsn


@pytest.fixture
def env(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD",
              "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_database_url_is_used_when_present(env):
    env.setenv("DATABASE_URL", "postgresql://u:secret@db:5432/ficatlas")
    assert dsn.default_database_url() == "postgresql://u:secret@db:5432/ficatlas"


def test_another_database_keeps_the_credentials(env):
    """The importer's scratch database is on the same server, so it needs the
    same password -- which lives in DATABASE_URL and nowhere else."""
    env.setenv("DATABASE_URL", "postgresql://u:secret@db:5432/ficatlas")
    got = dsn.default_database_url(dbname="ficatlas_import")
    assert got == "postgresql://u:secret@db:5432/ficatlas_import"


def test_no_password_literal_when_nothing_is_set(env):
    """libpq then tries ~/.pgpass or fails naming the user, which beats
    silently opening whatever a stale default still opens."""
    got = dsn.default_database_url()
    assert ":" not in got.split("://", 1)[1].split("@", 1)[0]


def test_a_generated_password_is_percent_encoded(env):
    env.setenv("POSTGRES_PASSWORD", "a+b/c=d")
    got = dsn.default_database_url()
    assert "a+b/c=d" not in got
    assert "a%2Bb%2Fc%3Dd" in got
