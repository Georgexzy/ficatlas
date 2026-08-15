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

```bash
# Frontend unit tests (vitest + happy-dom). Runs in the built image, so no
# node_modules on the host. Covers lib/ only — this is not a component-testing
# setup and does not render React.
docker compose run --rm --no-deps -T frontend npx vitest run

# Against working-copy sources without rebuilding the image:
docker compose run --rm --no-deps -T \
  -v "$PWD/frontend/lib:/app/lib" frontend npx vitest run
```

## Live workflow

- Backend + worker mount the repo as `/app` (live reload). `docker compose restart backend worker` to pick up changes.
- Frontend is a baked build: `docker compose build frontend && docker compose up -d frontend`.
- `init_db.py` runs in the backend/worker `lifespan` at every startup (idempotent DDL). New indexes added there are built **non-concurrently** at startup — fine for fresh installs, but for the live big table run a one-off `CREATE INDEX CONCURRENTLY` first so a restart doesn't block writes.
- The backend SQLAlchemy session (`db/session.py`) sets a statement timeout (~60s) by default; long scripts call `SET statement_timeout = 0` first.
- `db/` container is `ficatlas-db-1`; psql via `docker exec ficatlas-db-1 psql -U ficatlas -d ficatlas`.

## The public tier (ficatlas.com)

A second compose project, `ficatlas-public`, on the same box and **the same
database** as the dev stack. Full notes in `deploy/README.md`; the shape:

```
visitor → Cloudflare (TLS) → cloudflared → nginx :8080 → web-{blue,green} :3000
                                             nginx :8081 ← (Next rewrites /api/*)
                                                          → api-{blue,green} :8000
```

- `deploy/promote.sh` is the only way to deploy: it builds tagged by commit SHA,
  starts the idle colour, waits for health, repoints nginx, verifies through it,
  and keeps the old colour for 120s so `--rollback` is a reload.
- cloudflared shares nginx's network namespace, so the tunnel's service URL is
  `http://localhost:8080` — see the HTTPS note in `deploy/README.md` before
  concluding anything is served in the clear.
- There is **no worker in the public project**. The dev stack's worker does all
  indexing, and the public site sees it immediately because the database is
  shared. Restarting or rebuilding the public tier does not pause indexing.
- Signup is **open** (`SIGNUP_MODE=open`), which is a decision rather than a
  default left unset: a public search engine wants accounts. The invite path
  still exists — `SIGNUP_MODE=invite` plus a single shared `SIGNUP_CODE` in
  `.env`, not per-person invites — and the login form asks the server which mode
  it is in via `/api/auth/signup-policy`, so switching needs no frontend change.
  See `backend/api/auth.py`.

## Gotchas
- Never point a DB test at the live index — `conftest.py` refuses unless the DB
  name ends in `_test`.
- `ix_stories_title_trgm`/`summary_trgm` are on the **plain** columns, not
  `lower()`/`fic_doc`: the predicate is `ILIKE '%x%'` and GIN `gin_trgm_ops`
  lowercases internally. Keep them aligned with `api/search.py` predicates.
- README figures are checked by `python3 tests/check-readme.py` — run it after
  editing README numbers.
- **Do not re-add `ix_stories_tags_trgm` / `ix_stories_relationships_trgm`.** They
  were 4.4GB with zero scans and zero code references (tag and relationship
  filtering uses facet resolution + array containment `&&`, served by the plain
  GIN indexes). Dropping them took the DB 40GB → 36GB. `ix_stories_fandoms_trgm`
  and `ix_stories_characters_trgm` are still used and must stay.
- **One hostname, or sessions break.** The session cookie is host-only by design
  (no `Domain`), so every hostname that serves the app has its own cookie jar.
  `www.ficatlas.com` used to answer 200 with the whole site, which from the
  inside is indistinguishable from being signed out — and from "stay signed in
  didn't work", and from "owner-only pages 403 sometimes", depending on which
  host you landed on. `deploy/nginx.conf` now 301s `www.*` to the apex from a
  regex `server_name` block (matched ahead of the `_` default server). Adding a
  new hostname without a redirect re-creates all three symptoms at once.
- nginx.conf is bind-mounted, so `promote.sh --reload` cannot pick up a change to
  it: the container must be recreated (`docker compose -p ficatlas-public
  -f docker-compose.public.yml up -d --force-recreate nginx cloudflared` —
  cloudflared shares nginx's netns, so it goes with it). `check_nginx_conf` in
  promote.sh compares md5s and warns, which is the only reason this is ever
  noticed. Editing the file replaces the inode, so the running container keeps
  serving the OLD content until recreated — verify with
  `docker exec <nginx> md5sum /etc/nginx/nginx.conf` against the host copy.
- **`server_name _` is not a wildcard.** It matches nothing; the public block only
  ever served the site because it was the first block on `:8080` and nginx falls
  back to the first one. Adding any server block above it silently steals that
  role — which took the apex down for a minute (`301 https:///`, empty capture)
  the first time the www redirect went in. The public block now says
  `default_server` explicitly. Add new blocks freely, but never remove that.
- **A 500 with nothing in the API log is a proxy failure, not an app failure.**
  Check `docker logs <web-colour>` for `socket hang up`/`ECONNRESET`: the request
  died between Next and nginx and never reached uvicorn. It used to hit the first
  request after any quiet spell (an idle night, so the first click of the
  morning). Fixed by `keepalive_timeout 0` on nginx's internal `:8081` listener —
  see the comment there before re-enabling pooling on that hop.
- Anonymous traffic lives in `backend/tracking.py` (buffered writer, daily-rotating
  keyed visitor hash, 90-day retention) + `backend/api/traffic.py` (public
  `POST /hit` beacon, owner-only reports) + the Traffic tab on `/admin`.
  Pageviews come from the browser (`NavRecorder`), searches from a middleware in
  `main.py`, and the result count is stashed on `request.state.search_total` by
  `_note_total` next to each of search()'s three exits. No IP, user agent or
  account id is stored — see the module docstring before adding a column.
- `init_db.py`'s DDL is split by `_split_statements`, which drops whole-line `--`
  comments but still splits on a semicolon in a TRAILING inline comment. A
  `CREATE TABLE` with `-- how many it found; NULL if unknown` on a column line is
  cut in half and fails with "syntax error at end of input" — while every
  statement around it succeeds and startup logs nothing above a skipped-statement
  count. Keep semicolons out of inline DDL comments.
- Never put a `:param` token inside a `--` comment in a `text()` query.
  SQLAlchemy binds it there too and psycopg2 substitutes into the comment, so any
  value containing a newline escapes into executable SQL. This stalled series
  detection indefinitely (`syntax error at or near "twitter"`, from a Blogger
  share widget scraped as an author name). `tests/test_series_detect_sql.py`
  guards it at source.
- The search cache is two-tier: in-process L1 plus a shared UNLOGGED
  `search_cache_entries` table, because the per-worker cache meant four uvicorn
  workers each paid a ~10s miss for the same popular query. Bump
  `SCHEMA_VERSION` in `search_cache.py` when the search response shape changes.
- `tests/conftest.py` applies `init_db.py`'s DDL to the test database, so new
  tables are covered automatically. Before that, the schema was whatever had been
  created by hand and drifted silently.
- The AO3 stale-WIP refresh reads from `ao3_refresh_queue` rather than ranking on
  every cycle — the ranking query measured 36.3s and ~8.6GB of reads to pick 40
  works, hourly. Change the scoring freely; just keep it behind the queue.
- **Next config is baked into the image, so anything the config reads is a build
  arg, not a runtime variable.** `headers()` and `rewrites()` are resolved during
  `next build` into `.next/routes-manifest.json`, and `NEXT_PUBLIC_*` is
  substituted textually by the bundler. Three separate outages came from this:
  `FORCE_HTTPS` (CSP silently missing `upgrade-insecure-requests`),
  `INTERNAL_API_URL` (the entire public API 500ing, because the baked
  `http://backend:8000` does not resolve on the public network), and
  `NEXT_PUBLIC_SITE_URL` (the sitemap advertising `http://localhost:3000` URLs,
  which Google discards wholesale). All three are `ARG`s in `frontend/Dockerfile`
  and are passed by `promote.sh`. The tell is always the same: the variable is
  present in `docker inspect` on a running container while the thing it controls
  is wrong. Check the manifest, not the environment:
  `docker exec <web> grep -o 'http://[a-z0-9:.-]*' .next/routes-manifest.json`
- ~65% of the index has no summary, and it is not a bug in the crawler: 12.9M AO3
  rows came from the bulk metadata dump, which has no summary field at all
  (`backend/data/ao3_meta/` — keys are id, title, metadata). Freshly crawled AO3
  works do get summaries. FF.net is at 0% missing. Anything that reasons about
  search relevance should know that `fic_doc` is missing summary text for most
  AO3 works, and that the only way to fill it is re-crawling.
