"""Idempotent DB initialisation — safe to run multiple times."""
import os
from sqlalchemy import text
from models.story import get_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ficatlas:ficatlas@localhost:5432/ficatlas")
engine = get_engine(DATABASE_URL)

SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS stories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site VARCHAR NOT NULL,
    site_id VARCHAR(64) NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author VARCHAR(255),
    author_url TEXT,
    summary TEXT,
    language VARCHAR(32) DEFAULT 'English',
    rating VARCHAR(32),
    status VARCHAR(32) DEFAULT 'unknown',
    is_crossover BOOLEAN DEFAULT FALSE,
    word_count BIGINT DEFAULT 0,
    chapter_count INTEGER DEFAULT 1,
    chapter_count_total INTEGER,
    kudos INTEGER DEFAULT 0,
    bookmarks INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    favourites INTEGER DEFAULT 0,
    fandoms TEXT[] DEFAULT '{}',
    characters TEXT[] DEFAULT '{}',
    relationships TEXT[] DEFAULT '{}',
    tags TEXT[] DEFAULT '{}',
    warnings TEXT[] DEFAULT '{}',
    categories TEXT[] DEFAULT '{}',
    genres TEXT[] DEFAULT '{}',
    ao3_archive_warnings TEXT[] DEFAULT '{}',
    ffnet_category VARCHAR(128),
    is_hosted BOOLEAN DEFAULT FALSE,
    wayback_url TEXT,
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    crawled_at TIMESTAMPTZ DEFAULT NOW(),
    indexed_at TIMESTAMPTZ DEFAULT NOW(),
    search_vector TEXT
);

-- Migrations for existing installs
ALTER TABLE stories ADD COLUMN IF NOT EXISTS is_hosted BOOLEAN DEFAULT FALSE;
ALTER TABLE stories ADD COLUMN IF NOT EXISTS wayback_url TEXT;

CREATE TABLE IF NOT EXISTS chapters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT,
    summary TEXT,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0,
    posted_at TIMESTAMPTZ,
    start_note TEXT,
    end_note TEXT
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site VARCHAR NOT NULL,
    job_type VARCHAR(32),
    status VARCHAR(32) DEFAULT 'pending',
    stories_found INTEGER DEFAULT 0,
    stories_new INTEGER DEFAULT 0,
    stories_updated INTEGER DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_stories_site_site_id ON stories (site, site_id);
CREATE INDEX IF NOT EXISTS ix_stories_word_count ON stories (word_count);
CREATE INDEX IF NOT EXISTS ix_stories_updated_at ON stories (updated_at);
CREATE INDEX IF NOT EXISTS ix_stories_rating ON stories (rating);
CREATE INDEX IF NOT EXISTS ix_stories_status ON stories (status);
CREATE INDEX IF NOT EXISTS ix_stories_is_hosted ON stories (is_hosted);
CREATE INDEX IF NOT EXISTS ix_stories_fandoms ON stories USING gin (fandoms);
CREATE INDEX IF NOT EXISTS ix_stories_tags ON stories USING gin (tags);
CREATE INDEX IF NOT EXISTS ix_stories_relationships ON stories USING gin (relationships);
CREATE INDEX IF NOT EXISTS ix_stories_characters ON stories USING gin (characters);
CREATE INDEX IF NOT EXISTS ix_stories_search_vector ON stories USING gin (
    to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(author,''))
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_chapters_story_number ON chapters (story_id, number);

-- Idempotent schema additions for existing deployments
ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS ix_stories_cross_post_urls ON stories USING gin (cross_post_urls);

-- Performance note: we previously tried a functional trigram index on
-- array_to_string(fandoms, ',') to speed up substring fandom matching, but
-- array_to_string is not IMMUTABLE so Postgres rejects it in an index expression.
-- The plain GIN index on the fandoms array (ix_stories_fandoms, created above)
-- already serves array containment/overlap, which is what most queries use.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Composite index for the most common access pattern: filter by word_count, sort by kudos.
CREATE INDEX IF NOT EXISTS ix_stories_kudos_desc ON stories (kudos DESC);
CREATE INDEX IF NOT EXISTS ix_stories_wc_kudos ON stories (word_count, kudos DESC);

-- User accounts (added in user-accounts release)
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users (username);

CREATE TABLE IF NOT EXISTS user_sessions (
    token       VARCHAR(80) PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMP NOT NULL,
    last_used   TIMESTAMP DEFAULT NOW(),
    user_agent  VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user ON user_sessions (user_id);

CREATE TABLE IF NOT EXISTS user_data (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        VARCHAR(50) NOT NULL,
    value      JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS ix_user_data_user ON user_data (user_id);

-- Facets table for tag autocomplete. Populated on demand by /api/stats/refresh-facets
-- (or lazily). Holds distinct fandom/relationship/character/freeform values with
-- their story counts so the suggest endpoint is instant instead of scanning 2.3M rows.
CREATE TABLE IF NOT EXISTS facets (
    kind   VARCHAR(20) NOT NULL,   -- fandom | relationship | character | tag
    value  TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, value)
);
CREATE INDEX IF NOT EXISTS ix_facets_kind_value_trgm ON facets USING gin (value gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_facets_kind_count ON facets (kind, count DESC);
"""

def init():
    """Run each DDL statement in its own transaction so a single failure (e.g. a
    bad index on an older Postgres) can't roll back the creation of every table
    after it. Failures are logged, not fatal — the app should still boot with
    whatever succeeded."""
    statements = [s.strip() for s in SQL.split(";") if s.strip()]
    failed = []
    for stmt in statements:
        try:
            with engine.connect() as conn:
                conn.execute(text(stmt))
                conn.commit()
        except Exception as e:
            # First line of the statement for a readable log, plus the error.
            head = stmt.splitlines()[0][:80]
            failed.append((head, str(e).splitlines()[0]))
    if failed:
        print(f"Database initialised with {len(failed)} skipped statement(s):")
        for head, err in failed:
            print(f"  - SKIPPED: {head}… ({err})")
    else:
        print("Database initialised.")

if __name__ == "__main__":
    init()
