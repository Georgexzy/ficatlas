"""Idempotent DB initialisation — safe to run multiple times."""
import os
from sqlalchemy import text
from models.story import get_engine
from db.dsn import default_database_url

DATABASE_URL = os.environ.get("DATABASE_URL") or default_database_url(host="localhost")
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
-- The hosted library shelf is ORDER BY indexed_at DESC over a tiny subset of a
-- 39GB table. A bare `is_hosted` index serves neither the count nor the sort
-- well: Postgres read ~22k scattered heap pages to page five rows (16s, and past
-- the 60s statement timeout once the cache was cold). PARTIAL so it holds only
-- the ~30k hosted rows (a full composite over 19.9M rows was 148MB for the same
-- effect); the WHERE clause lets the planner use it for both queries.
CREATE INDEX IF NOT EXISTS ix_stories_hosted_shelf
    ON stories (indexed_at DESC) WHERE is_hosted;
CREATE INDEX IF NOT EXISTS ix_stories_fandoms ON stories USING gin (fandoms);
CREATE INDEX IF NOT EXISTS ix_stories_tags ON stories USING gin (tags);
CREATE INDEX IF NOT EXISTS ix_stories_relationships ON stories USING gin (relationships);
CREATE INDEX IF NOT EXISTS ix_stories_characters ON stories USING gin (characters);
CREATE UNIQUE INDEX IF NOT EXISTS ix_chapters_story_number ON chapters (story_id, number);

-- Idempotent schema additions for existing deployments
ALTER TABLE stories ADD COLUMN IF NOT EXISTS cross_post_urls TEXT[] DEFAULT '{}';

-- Delisting: the metadata entry itself withdrawn, not just the hosted text.
--
-- text_withdrawn_at hides the story's TEXT and leaves the listing, which is the
-- right default: the listing is a title, an author and a link to where the work
-- actually lives, so it keeps the author findable. But the takedown form has
-- always offered "if you want the listing gone as well, say so below", and
-- there was nothing behind that sentence. An author who asks for their name off
-- the index has a reason, and a search engine that cannot honour it is one that
-- made a promise it could not keep.
--
-- A flag rather than a delete, for the same reason takedowns never delete: the
-- row is needed for dedup and cross-post matching, and a mistaken or malicious
-- request has to be reversible.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS delisted_at TIMESTAMPTZ;
ALTER TABLE stories ADD COLUMN IF NOT EXISTS delisted_reason TEXT;
-- Partial: delisted rows are a vanishing fraction of 19.7M, and every search
-- adds "AND delisted_at IS NULL", so the useful index is the one over the few
-- rows that are set.
CREATE INDEX IF NOT EXISTS ix_stories_delisted ON stories (id) WHERE delisted_at IS NOT NULL;

-- Give the planner statistics for the columns added by ALTER TABLE above.
--
-- ADD COLUMN does not create statistics and does not move the row-modification
-- counter, so autoanalyze never fires for it: a column added to an existing
-- table can stay unanalysed indefinitely. With no stats the planner falls back
-- to a default selectivity for `IS NOT NULL` and decides a sequential scan is
-- cheaper than the partial index built specifically for that predicate.
--
-- Measured on the live index: `count(*) WHERE delisted_at IS NOT NULL` took
-- 15.0 SECONDS as a parallel seq scan over 19.9M rows, against 0.026ms as an
-- index-only scan once the column had been analysed. The admin page wraps that
-- count in a 4s timeout, so it silently rendered "—" instead of a number, and
-- the failure looked like the health panel being broken rather than a missing
-- ANALYZE.
--
-- Guarded on pg_stats so it runs once rather than on every startup: sampling
-- these columns takes ~10s on a table this size, which is not something to pay
-- for on each restart. A fresh install analyses an empty table instantly and
-- autoanalyze handles it from there.
-- Keyed on each column, not on one of them.
--
-- The first version of this guard asked whether `delisted_at` had statistics and
-- analysed the whole set if not. That works exactly once: after it runs,
-- delisted_at has stats, the guard never fires again, and any column added later
-- is silently skipped. source_restricted_at was added an hour after this was
-- written and immediately reproduced the 15-second seq scan the guard exists to
-- prevent — the same defect, by way of the fix for it.
--
-- Now each column is checked on its own and only the ones actually missing
-- statistics are analysed, so adding a name to the list is enough.
DO $ANALYSE$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(quote_ident(col), ', ')
      INTO missing
      FROM unnest(ARRAY['delisted_at', 'text_withdrawn_at',
                        'source_restricted_at']) AS col
     -- Only columns that EXIST yet. This block runs at line ~150 while
     -- source_restricted_at is added at ~540, so on a fresh install the whole
     -- DO block failed with "column does not exist" and no column got analysed
     -- — including delisted_at, which is the one the 15-second seq scan was
     -- about. Idempotent DDL is order-independent only if each statement is.
     WHERE EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'stories' AND column_name = col)
       AND NOT EXISTS (SELECT 1 FROM pg_stats
                        WHERE tablename = 'stories' AND attname = col);
    IF missing IS NOT NULL THEN
        EXECUTE format('ANALYZE stories (%s)', missing);
    END IF;
END
$ANALYSE$;
CREATE INDEX IF NOT EXISTS ix_stories_cross_post_urls ON stories USING gin (cross_post_urls);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Row-count sampling for the admin coverage panel. Counting "how many AO3 rows
-- are stubs" exactly means a filtered scan of 13.1M rows; a 200k sample answers
-- it to a fraction of a percent and returns immediately.
CREATE EXTENSION IF NOT EXISTS tsm_system_rows;

-- Title lookups for search ranking. Without this, guaranteeing that an exact
-- title match reaches the ranker cost a 24-SECOND sequential scan; with it, the
-- same lookup is 0.33ms. See api/search.py for why the guarantee is needed.
CREATE INDEX IF NOT EXISTS ix_stories_title_lower ON stories (lower(title));
-- Lets search ask "is this query the name of a fandom/ship/tag, or the name of
-- a work?" per request. 277ms as a sequential scan, 0.1ms with this.
CREATE INDEX IF NOT EXISTS ix_facets_value_lower ON facets (lower(value), count DESC);

-- ── Series ──────────────────────────────────────────────────────────────────
-- Works that belong together and have a reading order.
--
-- AO3 has series as a first-class thing; FanFiction.net and FictionAlley never
-- did, so authors there signal it in titles and summaries ("Sequel to X",
-- "Book 2 of the Y series") or not at all. Our bulk dumps carry no series field
-- from ANY source, so every row here is derived.
--
-- `source` records how, and it is not decoration: an inferred grouping can be
-- wrong, and a reader deciding what to read next deserves to know whether the
-- order came from the author or from us guessing.
CREATE TABLE IF NOT EXISTS series (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    author      TEXT,
    site        TEXT,
    source      TEXT NOT NULL DEFAULT 'inferred',   -- 'explicit' | 'inferred'
    confidence  REAL NOT NULL DEFAULT 0.5,
    work_count  INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_series_key ON series (lower(coalesce(author,'')), lower(name));
CREATE INDEX IF NOT EXISTS ix_series_author ON series (lower(coalesce(author,'')));

CREATE TABLE IF NOT EXISTS series_works (
    series_id  UUID NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    story_id   UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    -- Author-assigned where we can read one, otherwise publication order, which
    -- is what AO3 itself falls back to when a series has no positions set.
    position   INT,
    PRIMARY KEY (series_id, story_id)
);
CREATE INDEX IF NOT EXISTS ix_series_works_story ON series_works (story_id);
-- Main sequence or side story.
--
-- A series is usually not a flat list. The Dangerverse has five novels of
-- 215k-520k words that the author numbered "first" through "fifth", and five
-- companion pieces of 1.8k-49k that simply mention the name. Presenting those
-- ten as one numbered run tells a reader to read a 1,843-word vignette between
-- two 500,000-word novels.
ALTER TABLE series_works ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'main';
CREATE INDEX IF NOT EXISTS ix_series_works_order ON series_works (series_id, position NULLS LAST);

-- AO3 series id, for explicit series keyed on the archive's own identifier.
-- Partial unique index: inferred/stated series have no AO3 id, and many rows
-- share NULL. ON CONFLICT in ao3_series.record MUST repeat the WHERE clause.
ALTER TABLE series ADD COLUMN IF NOT EXISTS ao3_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ix_series_ao3 ON series (ao3_id) WHERE ao3_id IS NOT NULL;

-- Denormalised membership flag for the in_series search filter. See Story.has_series.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS has_series boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS ix_stories_has_series ON stories (id) WHERE has_series;

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
-- Only fandoms keeps a trigram index, and only because one query still needs it:
-- the "Surprise me" endpoint filters with fic_arr(fandoms) ILIKE (see
-- api/search.py). Everything else moved to facet resolution plus array
-- containment years ago — the note below records that trigram cost 3,682ms
-- against 516ms for containment — and the indexes for the other columns simply
-- carried on being built and maintained with nothing reading them.
--
-- Measured before removing them: zero scans against 3,383MB for tags and
-- 1,029MB for relationships. Dropping both took the database from 40GB to 36GB,
-- which on a box where search is bound by cache misses is not housekeeping —
-- every gigabyte not spent on a dead index is a gigabyte of page cache holding
-- something a query will actually read, and one fewer index to maintain on
-- every insert the harvests make.
--
-- If a query ever needs ILIKE over tags or relationships again, build it
-- CONCURRENTLY on the live index rather than adding it back here: a
-- non-concurrent GIN build over 19.9M rows blocks writes for the duration.
CREATE INDEX IF NOT EXISTS ix_stories_fandoms_trgm       ON stories USING gin (fic_arr(fandoms) gin_trgm_ops);
-- characters keeps its trigram index too: it is being scanned in practice, so
-- whatever reaches it, removing it from a fresh install would be a regression
-- nobody would notice until a query went from milliseconds to a seq scan over
-- 19.9M rows. Only the two with demonstrably ZERO scans were dropped.
CREATE INDEX IF NOT EXISTS ix_stories_characters_trgm    ON stories USING gin (fic_arr(characters) gin_trgm_ops);

-- search_within (api/search.py:557) is a leading-wildcard ILIKE over title OR
-- summary. A btree cannot serve a '%term%' pattern, so without trigram indexes
-- on the bare columns that filter fell back to a sequential scan of the whole
-- 19.7M-row table on every request that used it. gin_trgm_ops on the column
-- serves ILIKE '%term%' directly. (These are deliberately on the plain columns,
-- not lower()/fic_doc: the predicate is ILIKE, and the GIN trigram operator
-- class already lowercases internally.)
CREATE INDEX IF NOT EXISTS ix_stories_title_trgm   ON stories USING gin (title   gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_summary_trgm ON stories USING gin (summary gin_trgm_ops);

-- Exact author lookup, for "everything by this person" and for the cross-post
-- matcher. It MUST be queried as lower(author) = ... : Postgres cannot use a
-- functional index for an ILIKE even with no wildcards, and that form was a full
-- sequential scan — 9,995ms versus 6.4ms here, once per incoming story.
CREATE INDEX IF NOT EXISTS ix_stories_author_lower ON stories (lower(author));

-- Expression indexes get the default 100 statistics buckets on their expression,
-- which is far too coarse for trigram selectivity over 19.7M rows. Measured
-- before raising it: the planner estimated 1,038,590 rows for
-- fic_arr(fandoms) ILIKE '%Harry Potter%' against an actual 1,206,312, and
-- similar for the others.
--
-- This does NOT fix the remaining misestimate, and it is worth being clear about
-- that: the BitmapAnd of three such predicates is still costed at ~6,500 rows
-- when the real intersection is tens of thousands, because Postgres assumes the
-- filters are independent and "Harry Potter", "Fluff" and the word "harry" are
-- anything but. Extended statistics cannot express that for ILIKE-on-expression
-- predicates. Better per-index estimates are still worth having.
-- ix_stories_tags_trgm is deliberately NOT in this list. It was dropped (4.4GB,
-- zero scans -- tag filtering goes through facet resolution and array
-- containment), and the ALTER stayed behind throwing UndefinedTable on every
-- single startup. Harmless in itself, but it padded the skipped-statement count
-- that is the ONLY signal a real DDL statement failed to apply, so a genuine
-- failure looked like business as usual. Do not re-add it without the index.
ALTER INDEX ix_stories_fandoms_trgm ALTER COLUMN 1 SET STATISTICS 2000;
ALTER INDEX ix_stories_doc_fts      ALTER COLUMN 1 SET STATISTICS 2000;

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

-- Cross-archive popularity, 0..1. Written by popularity_rank.py, never by the
-- request path — see that file for why this is a percentile within each site
-- rather than a scaled raw count.
--
-- NULL means "no engagement figure recorded", which is most of the index and is
-- NOT the same as unpopular. The partial index matches the sort's own
-- `popularity IS NOT NULL` predicate so an unranked work costs nothing to skip.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS popularity REAL;
CREATE INDEX IF NOT EXISTS ix_stories_popularity ON stories (popularity DESC)
    WHERE popularity IS NOT NULL;

-- One partial index per remaining sort, for the same reason and on the same
-- pattern as ix_stories_popularity above.
--
-- Every sort in SORT_MAP orders the candidate set BEFORE it is capped, because
-- ordering after an unordered LIMIT ranks an arbitrary sample rather than the
-- index -- `fandoms=Harry Potter&sort=updated_desc` reported the newest work as
-- Feb 2026 when the true answer was 15 Aug 2026. Ordering first is only
-- affordable if the sort column is indexed, and unfiltered browse then becomes
-- a top-N walk instead of a sort of 20M rows (measured at 17.5s for hits and
-- 19.7s for published_at with no index).
--
-- Partial, because engagement is sparse and that makes these cheap: 791,477
-- works have hits, 950,479 comments, 941,998 bookmarks, out of 20.1M. The
-- predicates are the ones api/search.py adds for an unfiltered browse
-- (_PARTIAL_SORT_PREDICATE), so the planner can actually use them.
--
-- NOTE for the live database: these are created here NON-concurrently, which is
-- fine for a fresh install and would block writes on the big table at startup.
-- They were added to the live index with CREATE INDEX CONCURRENTLY first, so
-- this is a no-op there. Do the same for any new one.
CREATE INDEX IF NOT EXISTS ix_stories_hits_desc      ON stories (hits DESC)
    WHERE hits > 0;
CREATE INDEX IF NOT EXISTS ix_stories_comments_desc  ON stories (comments DESC)
    WHERE comments > 0;
CREATE INDEX IF NOT EXISTS ix_stories_bookmarks_desc ON stories (bookmarks DESC)
    WHERE bookmarks > 0;
CREATE INDEX IF NOT EXISTS ix_stories_published_desc ON stories (published_at DESC)
    WHERE published_at IS NOT NULL;

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

-- Did this session ask to be remembered across browser restarts?
--
-- The cookie cannot answer that later: a request carries a name and a value and
-- nothing about whether it had a Max-Age. Without somewhere to record it, the
-- sliding refresh in api/auth.py would re-issue every session as persistent and
-- quietly undo an unticked box on the first page load after signing in.
--
-- Defaults TRUE so sessions predating the column keep the behaviour they had.
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS remember BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS user_data (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        VARCHAR(50) NOT NULL,
    value      JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS ix_user_data_user ON user_data (user_id);

-- Shared search cache (see search_cache.py).
--
-- The in-process cache is per uvicorn worker, and WEB_CONCURRENCY is 4. Measured
-- on this box: the same popular query cost 11.0s, 9.7s and 6.9s on three
-- successive requests — each one warming a DIFFERENT worker — and only then
-- settled at 3ms. Four separate readers pay the full disk-bound cost of the same
-- search, and again every time the 120s TTL rolls over. The module docstring
-- always named this trade; what it costed it against was a cheap miss, not ten
-- seconds.
--
-- UNLOGGED on purpose, and the reason is not just speed:
--   * no WAL, so putting a write on the read path does not add replication or
--     fsync load to a box that is already disk-bound — which is the whole
--     problem being solved, and it would be self-defeating to make it worse;
--   * it is truncated after a crash, which for a cache is CORRECT. There is
--     nothing here that cannot be recomputed, and nothing that should survive
--     into a restarted database as stale truth.
--
-- Not Redis: a second service to hold recomputable data, competing for the page
-- cache that the actual fix (more of the index resident in RAM) needs. Postgres
-- is already there and already has all four workers connected to it.
CREATE UNLOGGED TABLE IF NOT EXISTS search_cache_entries (
    key        TEXT PRIMARY KEY,
    payload    TEXT        NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
-- Sweeping expired rows is the only scan this table ever takes.
CREATE INDEX IF NOT EXISTS ix_search_cache_expires
    ON search_cache_entries (expires_at);

-- Ranking queue for the AO3 stale-WIP refresh (worker.py).
--
-- The refresh loop picks the works most worth re-reading by a score combining
-- readership, how recently the work moved, and how long since we looked. That
-- score depends on now(), so it cannot be indexed and the ranking query has to
-- compute it for every candidate row and sort them.
--
-- Measured on live: EXPLAIN (ANALYZE, BUFFERS) put that at 36.3 SECONDS reading
-- 1,122,372 blocks -- about 8.6GB off disk -- at a 5% buffer hit rate, over 5.4M
-- candidate rows. It ran every REFRESH_INTERVAL_MIN (60) to choose
-- REFRESH_BATCH (40) works. Scoring 5.4 million rows to pick forty, hourly.
--
-- On this box that is not merely wasteful, it is the likely cause of the symptom
-- everything else was fighting: 8.6GB dragged through a page cache this size
-- evicts essentially all of it, which is why the buffer hit ratio sat at 47.5%
-- and why a cold search took ten seconds.
--
-- So the ranking is computed into this queue and consumed from it. The scoring
-- SQL is unchanged -- same works, same order, same reasons -- it simply runs
-- when the queue runs dry instead of every hour. At depth 2000 and 40 an hour
-- that is roughly every two days rather than 24 times a day.
--
-- UNLOGGED: losing it costs one refill, and it is rebuilt from scratch anyway.
CREATE UNLOGGED TABLE IF NOT EXISTS ao3_refresh_queue (
    story_id  UUID PRIMARY KEY,
    site_id   TEXT NOT NULL,
    score     DOUBLE PRECISION,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ao3_refresh_queue_score
    ON ao3_refresh_queue (score DESC NULLS LAST);

-- Facets table for tag autocomplete. Populated on demand by /api/stats/refresh-facets
-- (or lazily). Holds distinct fandom/relationship/character/freeform values with
-- their story counts so the suggest endpoint is instant instead of scanning 2.3M rows.
CREATE TABLE IF NOT EXISTS facets (
    kind   VARCHAR(20) NOT NULL,   -- fandom | relationship | character | tag
    value  TEXT NOT NULL,
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, value)
);
-- The same tag, spelled 44 ways.
--
-- Freeform tags are whatever an author typed, and the variation is not mostly
-- semantic — it is punctuation. "fluff" appears as Fluff, fluff!, fluff???,
-- #fluff, F L U F F, F.L.U.F.F., "Fluff", fluff~ and 36 more; Hurt/Comfort as
-- hurt comfort, hurt-comfort, hurt & comfort, hurt|comfort and 29 others.
-- 132,714 of 1,574,508 tag values (8.4%) differ from another only by case and
-- punctuation.
--
-- That splits a tag's count across its spellings, so autocomplete offers the
-- same tag repeatedly and ranks it below tags that happen to be spelled
-- consistently. Worse, search matched variants with ILIKE, which folds case and
-- NOT punctuation — so "Hurt/Comfort" never matched "hurt-comfort" at all.
--
-- `norm` is the mechanical part of the problem and only that: strip everything
-- that is not a letter or digit, lowercase the rest. It merges spellings; it
-- does NOT attempt to merge meanings. FanFicFare's maintainer is right that the
-- semantic half is impossible to automate (issue #1340: Naruto alone has ~300
-- tags meaning the same thing, and Humor/Humour/Comedy cannot be settled by any
-- rule) — this deliberately does not try.
ALTER TABLE facets ADD COLUMN IF NOT EXISTS norm TEXT;
CREATE INDEX IF NOT EXISTS ix_facets_kind_norm ON facets (kind, norm, count DESC);
CREATE INDEX IF NOT EXISTS ix_facets_kind_value_trgm ON facets USING gin (value gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_facets_kind_count ON facets (kind, count DESC);

-- Which section of a multi-part archive a work came from.
--
-- FictionAlley was not one archive but five, and readers navigated by them:
-- Schnoogle for novel-length work, The Dark Arts for horror, the Astronomy
-- Tower for romance, Riddikulus for humour, and a smaller essays-and-meta
-- section. The source dump carries this in a `site` column; the original import
-- read every other field and dropped that one, so 29,949 works arrived with the
-- distinction that organised the whole archive missing.
--
-- Generic rather than fictionalley-specific: SquidgeWorld and other Otwarchive
-- installs have collections, and FFN has categories, so the column is named for
-- what it means rather than for the first archive to need it.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS archive_section VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_stories_archive_section ON stories (archive_section)
    WHERE archive_section IS NOT NULL;

-- Role preview: an admin temporarily seeing the site as a lesser role.
--
-- Deliberately NOT user impersonation. The guidance on impersonation is that it
-- is a last resort — you inherit all of someone's confidential data and can act
-- as them, with no read-only mode — and none of that is needed to answer "what
-- does a reader see?". This downgrades your OWN effective role and touches
-- nobody else's account or data.
--
-- Enforced as a DOWNGRADE only (see auth.get_current_user): it can never raise
-- a role, so a stolen session cannot use it to gain anything.
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS view_as VARCHAR(16);

-- Private imports: a story whose text this ONE user may read.
--
-- The alternative was a per-user copy of the text, which would be wrong twice
-- over: the same fic imported by ten people would be stored ten times, and the
-- dedup and cross-post machinery works on `stories` rows, so parallel copies
-- would be invisible to it. Instead the story and its chapters live in the
-- shared tables exactly as they always have, `stories.is_hosted` stays FALSE so
-- it never enters the public shelf or the hosted counts, and this table is the
-- grant that lets one account read it.
--
-- Consequence worth stating: if two users import the same work, they share one
-- copy of the text and each holds their own grant. Neither can tell, and the
-- index does not grow twice.
CREATE TABLE IF NOT EXISTS user_hosted (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    story_id   UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, story_id)
);
CREATE INDEX IF NOT EXISTS ix_user_hosted_user ON user_hosted (user_id, created_at DESC);

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

-- The author locked the work to registered users at its source.
--
-- Distinct from text_withdrawn_at (we were asked to take it down) and from
-- delisted_at (we were asked to unlist it). This one is not a request to us at
-- all: it is a decision the author made on their own archive, which happens to
-- be visible to us because a restricted work redirects to /users/login instead
-- of loading. Roughly 966,000 of AO3's ~11.7M works are locked this way.
--
-- Recorded rather than acted on. The work still exists, so it stays indexed and
-- the listing still points at it — but "this author has taken the work out of
-- public view" is a fact worth holding, and withdraw_deleted.py was the only
-- thing in the system that could see it and threw it away. It is what any future
-- sitemap has to exclude: robots.txt permitting a crawl is passive, submitting a
-- URL is a positive act, and doing that for a work its author has hidden would
-- be indefensible.
--
-- Nullable and cleared on re-check, so unlocking a work undoes it.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS source_restricted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_stories_source_restricted ON stories (id)
    WHERE source_restricted_at IS NOT NULL;

-- Two tables that used to be created lazily by the first code path that needed
-- them: app_settings by api/settings.py's _ensure_table, crawl_budget by
-- ao3_budget.py. That is the hand-built drift conftest.py was introduced to end
-- -- both were absent from every test database, so nothing that touched site
-- settings or the crawl budget was ever covered, and a fresh install only got
-- them if the right request happened to run first.
--
-- Declared here so the schema has ONE source of truth. The lazy creators are
-- IF NOT EXISTS too, so they stay correct and simply become no-ops.
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_budget (
    host       TEXT PRIMARY KEY,
    next_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    interval_s DOUBLE PRECISION NOT NULL DEFAULT 5.0
);

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
-- Author permissions: the opposite of a takedown, and deliberately not its mirror.
--
-- Removal and permission are not symmetrical, and treating them as one system
-- would get one of them badly wrong:
--
--   REMOVAL needs no proof. Anyone can ask, the text hides immediately, and
--     nothing is deleted (see the takedowns table above). Over-honouring a
--     removal costs a work being hidden that need not have been — recoverable,
--     and the person harmed by getting it wrong is the one asking.
--
--   PERMISSION needs proof. "Yes, host my work" from an unverified form is
--     worthless: anyone could type any author's name and licence someone else's
--     writing. Over-honouring a permission means hosting without consent, which
--     is the harm this whole area exists to prevent, and it is not recoverable
--     by the person it happens to — they may never know.
--
-- So this table only ever holds VERIFIED statements, and the verification is
-- proof of control of the archive account: a nonce placed in the author's own
-- profile, which only they can edit. Nothing here is inferred from prose. In
-- particular, a fandom "blanket statement" is NOT consent — those are written
-- about transformative works (podfic, translation, remix) and read as permission
-- to rehost only if you want them to.
--
-- Keyed on (site, author) rather than per work, because that is what makes it
-- scale: one verification covers an author's whole back catalogue AND everything
-- they post later.
-- Crawlable entry points, one per fandom. See fandom_hubs.py for why these
-- exist at all (story pages had no inbound links a crawler could follow) and
-- why the works are precomputed rather than ranked per request.
--
-- Nothing writes to this at startup: it is built offline by fandom_hubs.py.
-- An empty table simply means no hubs are served, which is the correct
-- behaviour for a fresh install with nothing indexed yet.
-- FanFiction.net stories seen in the Internet Archive's index, waiting to be
-- fetched from there. Separate from wayback_queue because an FF.net story id
-- and an AO3 work id are different numbering spaces and would collide on a
-- shared primary key. See ffnet_wayback.py for why the archive is the only
-- route to FF.net at all.
-- Works a reader wants to hear about when they change.
--
-- The one thing every archive offers and a cross-archive index otherwise cannot:
-- "tell me when this updates", for works on AO3, FanFiction.net and FictionAlley in
-- one list rather than three sets of subscriptions.
--
-- No event fan-out. What the reader has already seen is recorded here, and an
-- update is a COMPARISON against the story row at read time — so a work is
-- correctly flagged whichever path updated it (live fetch, listing harvest,
-- Wayback, a bulk import), and a missed event cannot leave a follow
-- permanently stale. It also means no notification queue to drain, retry or
-- deduplicate.
CREATE TABLE IF NOT EXISTS follows (
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    story_id     UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    followed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The state at the moment the reader last looked. Both, because a work can
    -- gain a chapter without its timestamp moving and vice versa.
    seen_chapters INTEGER NOT NULL DEFAULT 0,
    seen_updated  TIMESTAMPTZ,
    PRIMARY KEY (user_id, story_id)
);
CREATE INDEX IF NOT EXISTS ix_follows_user ON follows (user_id, followed_at DESC);
CREATE INDEX IF NOT EXISTS ix_follows_story ON follows (story_id);

CREATE TABLE IF NOT EXISTS ffnet_wayback_queue (
    story_id    BIGINT PRIMARY KEY,
    snapshot_ts VARCHAR(20) NOT NULL,
    done_at     TIMESTAMPTZ,
    ok          BOOLEAN,
    -- 0 we hold nothing for this story, 1 it can still change, 2 it is finished.
    -- archive.org throttles this harvest to ~28 fetches an hour against ~108,000
    -- queued, so the order the queue is walked in decides what freshness we
    -- actually get. See ffnet_wayback.next_batch.
    priority    SMALLINT NOT NULL DEFAULT 1
);
ALTER TABLE ffnet_wayback_queue ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS ix_ffnet_wayback_priority
    ON ffnet_wayback_queue (priority, snapshot_ts DESC) WHERE done_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_ffnet_wayback_pending
    ON ffnet_wayback_queue (story_id) WHERE done_at IS NULL;

CREATE TABLE IF NOT EXISTS fandom_hubs (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    -- Every facet value that collapses to this fandom: "Harry Potter" and
    -- "Harry Potter - J. K. Rowling" are one hub, matched with && against the
    -- existing GIN index on stories.fandoms.
    variants    TEXT[] NOT NULL,
    work_count  INTEGER NOT NULL DEFAULT 0,
    top_ids     TEXT[] NOT NULL DEFAULT '{}',
    -- Top works PER SITE: {"ao3": [id, ...], "ffnet": [...], ...}.
    --
    -- One global ranking could only ever return AO3. kudos is the popularity
    -- column and it is present on 239,588 AO3 rows against 1,470 FanFiction.net
    -- rows out of 6.57M — so "most popular" sorted by kudos meant "AO3", every
    -- time, on every hub. Ranking within each site instead gives each archive
    -- its own list, which is also the honest presentation: a kudos count and a
    -- FanFiction.net favourite count are not the same unit and should never
    -- have been sorted against each other.
    top_by_site JSONB NOT NULL DEFAULT '{}'::jsonb,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The index listing orders by size, which is the only way it is ever read.
CREATE INDEX IF NOT EXISTS ix_fandom_hubs_count ON fandom_hubs (work_count DESC);

-- The same thing again, one per romantic pairing. See ship_hubs.py for why
-- ships get their own hubs rather than being a filter on a fandom hub: they are
-- how readers actually search, and they are the queries a fandom hub cannot
-- win because AO3 already owns them.
--
-- Identical shape to fandom_hubs deliberately -- hub_build.py writes both, and
-- the serving path in api/hubs.py reads both through one helper. `variants`
-- here holds every facet spelling that collapses to this pairing, matched with
-- && against the existing GIN index on stories.relationships.
--
-- Built offline by ship_hubs.py; an empty table simply means no ship hubs are
-- served, which is correct for a fresh install.
CREATE TABLE IF NOT EXISTS ship_hubs (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    variants    TEXT[] NOT NULL,
    work_count  INTEGER NOT NULL DEFAULT 0,
    top_ids     TEXT[] NOT NULL DEFAULT '{}',
    top_by_site JSONB NOT NULL DEFAULT '{}'::jsonb,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ship_hubs_count ON ship_hubs (work_count DESC);

-- One row per series the fill loop has tried, whatever the outcome.
--
-- Without it `incomplete_series` re-ranked the same handful every cycle: the
-- ordering favours the series missing the most works, and the worst of those
-- are larger than the page cap can ever fetch, so they could never stop being
-- the worst. The loop burned AO3 requests on five series indefinitely and
-- never reached the 42,563 it exists for.
CREATE TABLE IF NOT EXISTS series_fill_log (
    series_id    UUID PRIMARY KEY,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    complete     BOOLEAN NOT NULL DEFAULT false,
    listed       INTEGER
);
CREATE INDEX IF NOT EXISTS ix_series_fill_log_attempted ON series_fill_log (attempted_at);

-- Ship nicknames mined from the index, so free text can resolve "wolfstar" to
-- the pairing the archives file it under. Rebuilt whole by ship_aliases.py --
-- nothing here is authored, so there is nothing to preserve across a rebuild.
CREATE TABLE IF NOT EXISTS ship_aliases (
    alias        TEXT PRIMARY KEY,
    relationship TEXT NOT NULL,
    works        INTEGER,
    share        REAL,
    built_at     TIMESTAMP DEFAULT now()
);

-- When a hub's CONTENTS last changed, as opposed to when it was last rebuilt.
--
-- These are the sitemap's <lastmod>. built_at moves on every nightly rebuild
-- whether or not anything about the page differs, and a lastmod that claims
-- 7,584 pages changed every night is one Google learns to ignore -- their
-- guidance is explicit that it has to be accurate to be used. hub_build.py only
-- advances this when top_ids or work_count actually differ.
--
-- Defaults to now() so existing rows get a truthful-enough starting value
-- rather than NULL, and settles onto real change times after one rebuild.
ALTER TABLE fandom_hubs ADD COLUMN IF NOT EXISTS content_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE ship_hubs   ADD COLUMN IF NOT EXISTS content_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS author_permissions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site          VARCHAR(24) NOT NULL,          -- ao3 | ffnet
    -- Lowercased for lookup; author_display keeps what they actually type.
    author        TEXT NOT NULL,
    author_display TEXT,
    -- host          : full text may be stored and read here
    -- metadata_only : index the listing, never store the text
    -- deny          : do not index at all; existing rows are removed
    policy        VARCHAR(16) NOT NULL,
    -- Evidence, kept so a permission can be justified later rather than merely
    -- asserted. A consent you cannot demonstrate is not much use.
    verified_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    method        VARCHAR(24) NOT NULL DEFAULT 'profile_token',
    token         TEXT,                          -- the nonce that was matched
    evidence_url  TEXT,                          -- the page it was found on
    evidence_text TEXT,                          -- surrounding text as fetched
    contact_email TEXT,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Revocation must be at least as easy as granting. A revoked row is kept,
    -- not deleted, so "they once said yes and later said no" stays legible.
    revoked_at    TIMESTAMPTZ
);
ALTER TABLE author_permissions ADD COLUMN IF NOT EXISTS source_ip TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_author_permissions_site_author
    ON author_permissions (site, author);
CREATE INDEX IF NOT EXISTS ix_author_permissions_active
    ON author_permissions (site, author) WHERE revoked_at IS NULL;

-- Outstanding verification challenges. Short-lived: a nonce that never expires
-- is a nonce someone can find in an old profile edit and reuse.
CREATE TABLE IF NOT EXISTS author_permission_challenges (
    token       TEXT PRIMARY KEY,
    site        VARCHAR(24) NOT NULL,
    -- author is the lowercased lookup key; author_display is what they typed.
    -- Archive usernames are shown with case ("Georgexzy", not "georgexzy") and
    -- the first real verification recorded the lowered form as the display name,
    -- which is how an author's own name ends up written wrongly back at them.
    author      TEXT NOT NULL,
    author_display TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    attempts    INT NOT NULL DEFAULT 0,
    source_ip   TEXT
);
ALTER TABLE author_permission_challenges ADD COLUMN IF NOT EXISTS author_display TEXT;
CREATE INDEX IF NOT EXISTS ix_apc_expiry ON author_permission_challenges (expires_at);

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

-- ── Anonymous traffic ───────────────────────────────────────────────────────
--
-- What the site is used for, recorded without recording who used it. No IP
-- address and no user agent string are stored anywhere in here: `visitor` is a
-- keyed hash of both, mixed with the calendar day, so two requests can be told
-- apart within a day and cannot be joined across one. There is no account id
-- either — a signed-in reader is counted exactly like everybody else.
--
-- See tracking.py for the hashing and the write path.
CREATE TABLE IF NOT EXISTS visit_events (
    id       BIGSERIAL PRIMARY KEY,
    at       TIMESTAMP NOT NULL,
    visitor  CHAR(16) NOT NULL,
    kind     VARCHAR(8) NOT NULL,       -- page | search
    path     VARCHAR(300) NOT NULL,
    -- Referrer HOST, never the full referring URL: the path someone arrived
    -- from can name a person (a profile page, a private forum thread) and is
    -- not needed to answer "where does traffic come from".
    ref_host VARCHAR(120),
    q        VARCHAR(200),              -- the search text, for kind='search'
    -- How many that search found. NULL means no count was recorded, which is
    -- not the same as a search that found nothing.
    -- (No semicolon in this comment on purpose: _split_statements cuts the
    -- statement at one, and a CREATE TABLE truncated mid-column fails with
    -- "syntax error at end of input" while everything around it succeeds.)
    results  INTEGER,
    bot      BOOLEAN NOT NULL DEFAULT FALSE
);
-- Every report is "the last N days", so the ordering column is the one to
-- index. kind is in the second index because the searches report filters on it
-- before it sorts, over a table where pageviews will outnumber searches.
CREATE INDEX IF NOT EXISTS ix_visit_events_at ON visit_events (at DESC);
CREATE INDEX IF NOT EXISTS ix_visit_events_kind_at ON visit_events (kind, at DESC);

-- The key that turns an address into a `visitor` hash.
--
-- Its own table rather than app_settings, which GET /api/settings returns to
-- anyone who asks. A key that can be read is a key that can be used to test a
-- guess — "was this visitor at 81.2.x.y?" — and the whole point of hashing is
-- that nobody can answer that, including us.
CREATE TABLE IF NOT EXISTS tracking_secret (
    one_row BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (one_row),
    secret  TEXT NOT NULL
);
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


# Arbitrary but fixed: the key that says "someone is running startup DDL".
_INIT_LOCK_KEY = 8163264128


def init():
    """Run each DDL statement in its own transaction so a single failure (e.g. a
    bad index on an older Postgres) can't roll back the creation of every table
    after it. Failures are logged, not fatal — the app should still boot with
    whatever succeeded.

    Only one process runs it. This is called from the FastAPI lifespan, and with
    more than one uvicorn worker every worker has its own lifespan — so four
    workers meant four processes issuing the same ALTER TABLE and CREATE INDEX
    at once, each taking ACCESS EXCLUSIVE on `stories` and queueing behind the
    others. The statements are idempotent, so the result was survivable, but
    startup serialised on lock waits with a 5s lock_timeout underneath it.

    A session-level advisory lock makes the first worker do the work and the
    rest skip it. try_ rather than a blocking acquire: a worker that cannot get
    the lock has nothing to wait for, because whoever holds it is running the
    identical statements.
    """
    with engine.connect() as guard:
        got = guard.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _INIT_LOCK_KEY}
        ).scalar()
        if not got:
            print("Startup DDL already running in another worker; skipping.")
            return
        try:
            _init_locked()
        finally:
            guard.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _INIT_LOCK_KEY})
            guard.commit()


def _init_locked():
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
