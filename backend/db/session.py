"""Database session management"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ficatlas:ficatlas@localhost:5432/ficatlas"
)

# Pool and timeouts, sized against the threadpool that now serves requests.
#
# Handlers run in FastAPI's worker threads (they use blocking psycopg2, so
# `async def` froze the event loop — see api/search.py). AnyIO gives that pool 40
# threads by default, and each one wants a connection, so a 10+20 pool left a
# third of them queueing behind the pool instead of behind the database.
#
# Sized so PROD stays under Postgres max_connections=100: 30 per process x 2
# uvicorn workers = 60, plus the background worker's own, leaves headroom for
# psql and maintenance scripts. All overridable, because a bigger box wants
# bigger numbers and a smaller one cannot afford these.
# Per PROCESS, not per deployment. Each uvicorn worker builds its own engine and
# therefore its own pool, so the connection count is (pool + overflow) x workers
# x services. At the previous 20+10 that is 30 per worker: four workers plus the
# background worker's own pool is 150 against a max_connections of 100, and the
# failure is not gradual — the pool that loses the race raises
# "FATAL: sorry, too many clients already" on every request.
#
# So the configured numbers are a budget for the whole API and are divided by
# the worker count. The default drops to 12+6 per worker at WEB_CONCURRENCY=4,
# which leaves room for the worker service, psql and maintenance scripts.
WORKERS = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))
POOL_SIZE = max(2, int(os.getenv("DB_POOL_SIZE", "32")) // WORKERS)
MAX_OVERFLOW = max(1, int(os.getenv("DB_MAX_OVERFLOW", "16")) // WORKERS)
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))

# A query that runs forever holds a connection forever, and thirty of those is
# the whole pool — one pathological search taking the site down for everyone.
# The ceiling is generous because some legitimate searches over 19.7M rows are
# genuinely slow, but it is finite.
STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000"))

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,          # survives a database restart without 500s
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,   # fail fast rather than hang when saturated
    # Postgres and connection proxies drop idle connections; recycling below any
    # such limit means we never hand out one that the server has already closed.
    pool_recycle=1800,
    connect_args={
        "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        "connect_timeout": 10,
        "application_name": os.getenv("APP_NAME", "ficatlas"),
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    """Context manager for scripts/crawlers"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
