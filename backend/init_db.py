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
"""

def init():
    with engine.connect() as conn:
        for stmt in SQL.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
        conn.commit()
    print("Database initialised.")

if __name__ == "__main__":
    init()
