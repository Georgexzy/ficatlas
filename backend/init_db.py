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
     WHERE NOT EXISTS (SELECT 1 FROM pg_stats
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
CREATE INDEX IF NOT EXISTS ix_stories_fandoms_trgm       ON stories USING gin (fic_arr(fandoms) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_relationships_trgm ON stories USING gin (fic_arr(relationships) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_characters_trgm    ON stories USING gin (fic_arr(characters) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_stories_tags_trgm          ON stories USING gin (fic_arr(tags) gin_trgm_ops);

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
ALTER INDEX ix_stories_fandoms_trgm ALTER COLUMN 1 SET STATISTICS 2000;
ALTER INDEX ix_stories_tags_trgm    ALTER COLUMN 1 SET STATISTICS 2000;
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
