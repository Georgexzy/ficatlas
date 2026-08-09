# Notes for Claude

Quick orientation for an agent working on this repo. FicAtlas is a Dockerized
fanfiction search engine: Next.js 15 frontend (port 3000, reverse-proxies
`/api/*`) + FastAPI backend (8000) + PostgreSQL 16 (~19.7M `stories` rows).
Live tree is `/home/george/ficatlas` (not this worktree).

## Running tests

```bash
# Pure unit tests (no DB). Safe, fast.
docker exec ficatlas-backend-1 python -m pytest tests/ -q

# Integration tests against the throwaway DB. The DB tests are SKIPPED unless
# TEST_DATABASE_URL points at a database whose name ends in `_test` — that name
# check is the guardrail so they can never run against the live 19.7M-row index.
docker exec \
  -e TEST_DATABASE_URL="postgresql://ficatlas:<pw>@db:5432/ficatlas_test" \
  -e DATABASE_URL="postgresql://ficatlas:<pw>@db:5432/ficatlas_test" \
  ficatlas-backend-1 python -m pytest tests/ -q
```

Get the DB password from `docker inspect ficatlas-backend-1` (env `DATABASE_URL`).
`tests/conftest.py` builds a session and truncates all app tables per test.

## Live workflow

- Backend + worker mount the repo as `/app` (live reload). `docker compose restart backend worker` to pick up changes.
- Frontend is a baked build: `docker compose build frontend && docker compose up -d frontend`.
- `init_db.py` runs in the backend/worker `lifespan` at every startup (idempotent DDL). New indexes added there are built **non-concurrently** at startup — fine for fresh installs, but for the live big table run a one-off `CREATE INDEX CONCURRENTLY` first so a restart doesn't block writes.
- The backend SQLAlchemy session (`db/session.py`) sets a statement timeout (~60s) by default; long scripts call `SET statement_timeout = 0` first.
- `db/` container is `ficatlas-db-1`; psql via `docker exec ficatlas-db-1 psql -U ficatlas -d ficatlas`.

## Session work (this branch)

Author opt-out handling + Phase-1 audit fixes, deployed and tested. All uncommitted unless stated.

### Author opt-out
- `backend/external_optout.py` — conservative detector (`has_external_optout`,
  `match_external_optout`). Tier-1 "do not repost/redistribute/re-publish" verbs +
  Tier-2 put-on-a-site verbs that name an external surface; never matches grant
  language like "Licensed to translate and redistribute".
- `backend/live_fetch/persist.py::persist_live_results` — skips new opt-out rows
  and deletes existing ones (chapters cascade). Plus two swallowed-failure fixes:
  a cross-post lookup failure now logs instead of silently looking like "no
  match", and a cross-post merge failure now rolls back, logs, counts `failed`,
  and skips the insert instead of falling through and creating a duplicate.
- `backend/api/library.py::import_url` — public import with an opt-out summary
  → HTTP 403; private imports unaffected.
- `backend/optout_sweep.py` — one-shot cleanup, dry-run default, `--apply` deletes.
  Already run once against live (removed 49 works).

### Frontend back-nav (frontend/app/page.tsx)
- `lastSearchCache` — module-level cache keyed on exact query URL, written only
  after a fetch resolves, so back/forward to the same search renders instantly.
- Scroll capture guarded to the `/` path + trailing 150ms debounce, so a
  navigation in flight can't clobber the saved position with a story href or y=0.

### Phase-1 audit fixes
- `backend/api/admin.py::run_job` — replaced `asyncio.get_event_loop()` (raised
  RuntimeError on py3.10+ → every call 500'd) with loop-safe
  `run_in_background(lambda: asyncio.to_thread(_go))`.
- `backend/init_db.py` — added `ix_stories_title_trgm` + `ix_stories_summary_trgm`
  (GIN trigram) so `search_within` (leading-wildcard ILIKE on title/summary) uses
  an index instead of a seq scan. **Already built valid on live.** If you ever
  rebuild them on live, prefer `CREATE INDEX CONCURRENTLY` (and retry — the plain
  CONCURRENTLY validation failed once under continuous write load; a restart's
  non-concurrent build is the reliable fallback).

### Tests
- `backend/tests/conftest.py` (DB fixture, `_test`-DB guard)
- `backend/tests/test_external_optout.py` — 21 unit tests
- `backend/tests/test_persist_integration.py` — 11 DB tests (insert/dedup/opt-out/cross-post/enrich/merge)
- `backend/tests/test_import_url_optout.py` — 3 DB tests (403 public, allow private, allow non-opt-out)
- Full suite: **67 passed** with test DB; **53 passed / 14 skipped** without.

## Gotchas
- Never point a DB test at the live index — `conftest.py` refuses unless the DB
  name ends in `_test`.
- `ix_stories_title_trgm`/`summary_trgm` are on the **plain** columns, not
  `lower()`/`fic_doc`: the predicate is `ILIKE '%x%'` and GIN `gin_trgm_ops`
  lowercases internally. Keep them aligned with `api/search.py` predicates.
- README figures are checked by `python3 tests/check-readme.py` — run it after
  editing README numbers.
