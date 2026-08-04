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
CREATE UNIQUE INDEX IF NOT EXISTS ix_chapters_story_number ON chapters (story_id, number);

-- Idempotent schema additions for existing deployments
ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS ix_stories_cross_post_urls ON stories USING gin (cross_post_urls);

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Keep planner statistics fresh on the one table that matters.
--
-- The default analyze scale factor is 10% of the table, which never converges
-- under a long bulk import: a run that took stories from 2.2M to 18M rows left
-- the planner still working from statistics gathered at 2.2M. Every plan it chose
-- was wrong for the real table, and search went from 0.35s to 18.8s until an
-- explicit ANALYZE was run. 2% plus a large floor keeps stats close to reality
-- during imports without analyzing constantly on a quiet index.
ALTER TABLE stories SET (
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_analyze_threshold = 50000,
    autovacuum_vacuum_scale_factor = 0.05
);

-- ── Search indexes ──────────────────────────────────────────────────────────
-- An earlier attempt at trigram indexes on array_to_string(fandoms, ',') was
-- abandoned because array_to_string is only marked STABLE, so Postgres rejects it
-- in an index expression. The fix is to wrap it: array_to_string over text[] IS
-- genuinely deterministic (it is marked STABLE only because of the type output
-- functions it may call for non-text element types), so an IMMUTABLE SQL wrapper
-- restricted to text[] is sound and unlocks expression indexes.
--
-- Without these, free-text search and every fandom/ship/character/tag filter fell
-- back to a full sequential scan of the whole stories table on each request.
--
-- api/search.py builds its predicates from exactly these expressions. If you edit
-- one side you MUST edit the other, or the planner stops matching the index and
-- search silently reverts to seq scans.

CREATE OR REPLACE FUNCTION fic_arr(arr text[])
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$FIC$ SELECT array_to_string(coalesce(arr, '{}'::text[]), ',') $FIC$;

CREATE OR REPLACE FUNCTION fic_doc(
    title text, summary text, author text,
    fandoms text[], characters text[], relationships text[], tags text[]
) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$FIC$ SELECT coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(author,'')
          || ' ' || fic_arr(fandoms) || ' ' || fic_arr(characters)
          || ' ' || fic_arr(relationships) || ' ' || fic_arr(tags) $FIC$;

-- Free-text search: one GIN tsvector over everything a user searches by.
CREATE INDEX IF NOT EXISTS ix_stories_doc_fts ON stories USING gin (
    to_tsvector('english', fic_doc(title, summary, author, fandoms, characters, relationships, tags))
);

-- Facet filters use substring (ILIKE '%value%') matching so that a search for
-- "Harry Potter" also matches AO3's canonical "Harry Potter - J. K. Rowling".
-- Only a trigram index can serve a leading-wildcard LIKE.
CREATE INDEX IF NOT EXISTS ix_stories_fandoms_trgm       ON stories USING gin (fic_arr(fandoms) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_relationships_trgm ON stories USING gin (fic_arr(relationships) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_characters_trgm    ON stories USING gin (fic_arr(characters) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_tags_trgm          ON stories USING gin (fic_arr(tags) gin_trgm_ops);

-- Exact author lookup, for "everything by this person" and for the cross-post
-- matcher. It MUST be queried as lower(author) = ... : Postgres cannot use a
-- functional index for an ILIKE even with no wildcards, and that form was a full
-- sequential scan — 9,995ms versus 6.4ms here, once per incoming story.
CREATE INDEX IF NOT EXISTS ix_stories_author_lower ON stories (lower(author));

-- Date filtering and the "recently updated" sort both use
-- coalesce(updated_at, published_at): updated_at alone is set for 0.17% of rows,
-- so filtering or sorting on it returned essentially nothing.
CREATE INDEX IF NOT EXISTS ix_stories_last_activity
    ON stories (coalesce(updated_at, published_at) DESC NULLS LAST);

-- crawled_at is "when did we last verify this work". It is now advanced every
-- time a live blurb re-confirms a row, which makes it a real staleness signal —
-- it was previously set once at insert and never touched, so a row verified
-- minutes ago looked identical to one imported months earlier. Indexed for
-- "refresh the stalest works-in-progress first".
CREATE INDEX IF NOT EXISTS ix_stories_stale_wip
    ON stories (crawled_at ASC) WHERE status = 'in_progress';

-- Composite index for the most common access pattern: filter by word_count, sort by kudos.
CREATE INDEX IF NOT EXISTS ix_stories_kudos_desc ON stories (kudos DESC);

-- Drives the AO3 repair/harvest queue in ao3_title_repair.py: most-read works
-- first, but only among rows not already checked today so unreachable works
-- cannot camp at the head of the queue.
--
-- The expression list must stay identical to TRUNCATED_SQL's ORDER BY, down to
-- `AT TIME ZONE 'UTC'`. date_trunc over a timestamptz is only STABLE — it
-- depends on the session TimeZone — so it cannot be indexed at all without the
-- cast, and any mismatch silently degrades to a full scan: 11,816ms against
-- 57ms measured on 13M AO3 rows.
CREATE INDEX IF NOT EXISTS ix_stories_repair_queue ON stories (
    (date_trunc('day', crawled_at AT TIME ZONE 'UTC')) NULLS FIRST,
    ((COALESCE(kudos, 0) + COALESCE(hits, 0))) DESC,
    (COALESCE(word_count, 0)) DESC
) WHERE site = 'ao3'
    AND tags @> ARRAY['ao3_meta_dump']
    AND title ~* ' (and|of|the|with)$';

-- ── Dropped indexes ─────────────────────────────────────────────────────────
-- ix_stories_non_explicit: DROPPED. Defined as
--     btree (updated_at DESC) WHERE (rating)::text <> 'E'
--   but ratings are stored as 'explicit'/'teen'/'general'/'mature'/'not_rated'
--   and never as 'E', so the predicate was true for all 19.4M rows. It was not
--   a partial index at all — it was a full duplicate of ix_stories_updated_at,
--   182MB and a write on every insert, with 0 scans in the database's lifetime.
-- ix_stories_search_vector: 355MB GIN over a search_vector column that was never
--   populated (100% NULL) and never scanned. Superseded by ix_stories_doc_fts.
-- ix_stories_url: exact duplicate of the stories_url_key UNIQUE constraint index
--   (206MB each) — the constraint index is the one that must stay.
-- ix_stories_wc_kudos: never scanned, and redundant with the plain
--   ix_stories_word_count index (which is used) for the range-filter case.
DROP INDEX IF EXISTS ix_stories_search_vector;
DROP INDEX IF EXISTS ix_stories_url;
DROP INDEX IF EXISTS ix_stories_wc_kudos;

-- User accounts (added in user-accounts release)
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users (username);

-- Roles. Added after the fact, so: give existing rows the default, then promote
-- the OLDEST account to owner. On an instance that predates roles the first
-- account is the person who set the thing up, and leaving them as a reader
-- would lock them out of their own library.
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'reader';
UPDATE users SET role = 'owner'
WHERE id = (SELECT id FROM users ORDER BY created_at ASC LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'owner');

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

-- Optional contact address. Optional because the first accounts here were made
-- without one and requiring it retroactively would lock those users out; and
-- because a site that cannot reliably send mail (a home IP is poor at
-- deliverability) should not pretend an address is load-bearing.
ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(200);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_lower ON users (lower(email))
    WHERE email IS NOT NULL;

-- Password reset tokens. Only the HASH is stored: this table is in every
-- backup, and a plaintext token in a backup is a live key to an account.
CREATE TABLE IF NOT EXISTS password_resets (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    -- Set when there is no mail transport and an admin has to read the code out
    -- to the user by whatever channel they actually share.
    delivered  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_password_resets_open ON password_resets (created_at DESC)
    WHERE used_at IS NULL;

-- Takedown requests, and the flag that acts on them.
--
-- text_withdrawn hides the FULL TEXT only; the story stays in the index as
-- metadata plus a link to the original. That distinction is the whole point: an
-- author objecting to their work being rehosted here is not asking to be erased
-- from a search engine, and removing the row entirely would also mean the next
-- crawl happily re-imports it.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS text_withdrawn_at TIMESTAMPTZ;
ALTER TABLE stories ADD COLUMN IF NOT EXISTS text_withdrawn_reason TEXT;
CREATE INDEX IF NOT EXISTS ix_stories_text_withdrawn ON stories (id)
    WHERE text_withdrawn_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS takedowns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id     UUID REFERENCES stories(id) ON DELETE SET NULL,
    story_url    TEXT NOT NULL,          -- kept even if the story row goes
    claimant     TEXT NOT NULL,
    email        TEXT NOT NULL,
    relationship TEXT NOT NULL,          -- author | agent | other
    detail       TEXT,
    source_ip    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- pending -> upheld (text stays withdrawn) | rejected (text restored)
    state        VARCHAR(12) NOT NULL DEFAULT 'pending',
    resolved_at  TIMESTAMPTZ,
    resolved_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_takedowns_state ON takedowns (state, created_at DESC);

-- Strike record for withdraw_deleted.py. A single 404 during an AO3 deploy must
-- not withdraw thousands of stories, so a work has to be confirmed gone twice on
-- separate passes before its text is hidden.
CREATE TABLE IF NOT EXISTS source_gone (
    story_id       UUID PRIMARY KEY REFERENCES stories(id) ON DELETE CASCADE,
    strikes        INTEGER NOT NULL DEFAULT 0,
    last_checked   TIMESTAMPTZ,
    last_seen_gone TIMESTAMPTZ
);

-- AO3 work IDs discovered in the Wayback Machine's CDX index, waiting to have
-- their archived page fetched and parsed. See wayback_harvest.py: this is how
-- the 13M-row summary gap gets filled without any of it landing on AO3.
CREATE TABLE IF NOT EXISTS wayback_queue (
    work_id     BIGINT PRIMARY KEY,
    snapshot_ts VARCHAR(20) NOT NULL,   -- 14-digit Wayback capture timestamp
    done_at     TIMESTAMPTZ,
    ok          BOOLEAN
);
-- The claim query filters on done_at IS NULL and orders by work_id; once most
-- of the table is processed a full scan to find the unprocessed tail would
-- dominate, so index exactly that predicate.
CREATE INDEX IF NOT EXISTS ix_wayback_pending ON wayback_queue (work_id)
    WHERE done_at IS NULL;
"""

def _split_statements(sql: str) -> list[str]:
    """Split a DDL script into individual statements.

    Naively splitting on ";" breaks on two things that appear in this file:
    a semicolon inside a `--` comment (which silently produces a garbage
    fragment that then fails to execute), and a semicolon inside a
    dollar-quoted function body. Strip comments first, then split only on
    semicolons that sit outside a $tag$...$tag$ block.
    """
    import re

    stripped = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )

    statements, buf, dollar_tag = [], [], None
    for chunk in re.split(r"(\$[A-Za-z_]*\$|;)", stripped):
        if dollar_tag is None and re.fullmatch(r"\$[A-Za-z_]*\$", chunk or ""):
            dollar_tag = chunk          # entering a dollar-quoted body
        elif dollar_tag is not None and chunk == dollar_tag:
            dollar_tag = None           # leaving it
        elif chunk == ";" and dollar_tag is None:
            statements.append("".join(buf))
            buf = []
            continue
        buf.append(chunk or "")
    statements.append("".join(buf))
    return [s.strip() for s in statements if s.strip()]


def init():
    """Run each DDL statement in its own transaction so a single failure (e.g. a
    bad index on an older Postgres) can't roll back the creation of every table
    after it. Failures are logged, not fatal — the app should still boot with
    whatever succeeded."""
    statements = _split_statements(SQL)
    failed = []
    for stmt in statements:
        try:
            with engine.connect() as conn:
                # Never let startup block on someone else's transaction. The
                # ALTER TABLE statements need ACCESS EXCLUSIVE, which queues
                # behind ANY open transaction on `stories` — a background
                # backfill holding one idle-in-transaction hung the API's
                # lifespan indefinitely, so every request 500'd until the
                # transaction was killed by hand. These statements are all
                # idempotent, so timing out and retrying next boot is harmless;
                # hanging is not.
                conn.execute(text("SET lock_timeout = '5s'"))
                conn.execute(text("SET statement_timeout = '10min'"))
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
