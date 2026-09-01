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
- **A low Cloudflare cache ratio here is mostly arithmetic, not a fault.**
  Measured: 2,305 story requests hit 2,277 DISTINCT urls — a 1.2% repeat rate.
  Crawlers walk ~750k unique story pages, so almost every request is a first
  request and no cache can absorb it. The 6.7% hit ratio was read as a problem
  and largely is not one. Edge caching still earns its place for repeat
  visitors, for several search engines fetching the same page, and for
  re-crawls inside `stale-while-revalidate` — but do not expect it to move
  origin load much, and measure the repeat rate before claiming it will.
- **Documents are edge-cacheable but browser-revalidated, and the two halves live
  apart.** `next.config.ts` sends `public, max-age=0, must-revalidate,
  s-maxage=900` on `/story|series|fandom|ship|s/*` only; a Cloudflare cache rule
  (`deploy/cloudflare_cache_rule.py`) has to match the SAME paths with
  `respect_origin`, or the caching silently does not happen. `max-age=0,
  must-revalidate` is load-bearing — a bad CSP once went sticky in phone caches,
  and only shared caches read `s-maxage`. Safe to share between visitors *only
  because nothing under `frontend/app/` calls `cookies()`*: the server HTML is
  identical for everyone and reader state arrives after hydration. Adding a
  server component that reads the session would make these pages
  un-cacheable — check before you do.
- **No credential literals in tracked source, and a hook that enforces it.**
  `backend/db/dsn.py` composes the fallback DSN from `POSTGRES_*`; eighteen
  files used to carry `postgresql://ficatlas:<password>@…` instead. That was never
  the live password (which is in `.env`, never committed, and confirmed absent
  from every blob in history) but a scanner cannot tell, which is how the repo
  earned a GitGuardian alert. `tests/check-secrets.py` checks two things: that
  no value in `.env` appears in a tracked file, and that nothing credential-
  shaped is written down. Enable the hook on a fresh clone with
  `git config core.hooksPath .githooks` — cloning does not install it.
- Never point a DB test at the live index — `conftest.py` refuses unless the DB
  name ends in `_test`.
- **`backup.sh essential` selects text by `is_hosted OR a row in user_hosted`,
  and both halves are load-bearing.** A privately imported work is
  `is_hosted = false` PLUS a `user_hosted` row (`privatise_live_archive_hosting.py`),
  so the original `WHERE is_hosted` silently dropped 28 stories / 678 chapters of
  exactly the text the script exists to protect. `user_hosted` itself is dumped
  too — it is the ACCESS CONTROL for a private import, so text restored without
  it is in the database and reachable by nobody. Anything that changes what
  "hosted" means has to change this predicate in the same commit.
- `ix_stories_title_trgm`/`summary_trgm` are on the **plain** columns, not
  `lower()`/`fic_doc`: the predicate is `ILIKE '%x%'` and GIN `gin_trgm_ops`
  lowercases internally. Keep them aligned with `api/search.py` predicates.
- README figures are checked by `python3 tests/check-readme.py` — run it after
  editing README numbers.
- **There are two hub tables, built by one module.** `fandom_hubs` (5,025 rows,
  one per fandom) and `ship_hubs` (2,553 rows, one per romantic pairing) have an
  identical shape and are both written by `hub_build.build_groups`; `api/hubs.py`
  serves both through one pair of helpers, mounted at `/api/hubs` and
  `/api/ships`. They exist because search URLs are blocked in robots.txt, so a
  crawler needs bounded real pages to walk. Ships are the half that can rank —
  nothing outranks AO3 for "[fandom] fanfiction".
  - Ship slugs are ALPHABETICAL (`john-watson-sherlock-holmes`) so "A/B" and
    "B/A" collapse and the URL never changes; the DISPLAYED name is the
    most-used spelling ("Sherlock Holmes/John Watson"), because that is what the
    page's search link passes to facet resolution. Do not make them agree by
    changing the slug — popularity moves between rebuilds and would rename
    indexed URLs.
  - Romantic (`/`) only. Platonic (`&`) slugifies identically, so building both
    would merge a ship and a friendship onto one URL.
  - `--limit N` on either builder SKIPS the stale sweep. It used to prune
    regardless, so a `--limit 10` trial run deleted the other 5,015 hubs.
- **Story pages must keep a server-rendered link back to their hubs.** The client
  body links fandoms and ships to `/?fandoms=…`, which robots.txt blocks, so
  before `_hub_links` in `api/stories.py` every story page was a crawl dead end:
  hubs fed ~750k story pages and got nothing back. The `hubs` field on
  `StoryDetail` and the `.story-hubs` nav in `story/[id]/page.tsx` are that link,
  and it has to stay OUTSIDE `StoryClient` to be in the server HTML.
- **`popularity` is recomputed by the worker, not by hand.** `popularity_rank.py`
  had no loop, so the one sort that is honest across archives was frozen at
  whatever the last manual run produced — 550,384 works scored against 1,078,121
  carrying an engagement signal, so half the eligible index sorted behind
  everything. `_popularity_loop` runs it weekly (`REBUILD_POPULARITY`,
  `POPULARITY_INTERVAL_HOURS`). Do not remove it and go back to running the
  script by hand.
- **Do not re-add `ix_stories_tags_trgm` / `ix_stories_relationships_trgm`.** They
  were 4.4GB with zero scans (tag and relationship filtering uses facet
  resolution + array containment `&&`, served by the plain GIN indexes).
  Dropping them took the DB 40GB → 36GB. `ix_stories_fandoms_trgm`
  and `ix_stories_characters_trgm` are still used and must stay.
  - **"zero code references" was wrong**, and it cost 83 seconds a query.
    `arr_inc_aliased` fell back to `fic_arr(col) ILIKE '%…%'` whenever the alias
    table had nothing — which is any pairing where either half is outside the
    ~40 Harry Potter characters, i.e. most of the index. With the index gone
    that fallback was a sequential scan of 20M rows: `relationships=Theodore
    Nott/Luna Lovegood` took 83.5s and 500'd through the proxy, on the exact
    link every ship hub emits. It now resolves against the facets table first,
    like `arr_inc` always did. The trigram branch still exists as a last resort
    — if you see a filtered search take a minute, that is where it went.
- **Ship filters must try both pairing orders.** The vocabulary lookup is a
  substring match, so it only finds the order the reader typed, and the archives
  are not consistent: "Theodore Nott/Luna Lovegood" resolved to 3 works while
  "Luna Lovegood/Theodore Nott" — the same ship — carried 564. `_both_orders`
  in `api/search.py` handles it for two-part pairings; the ship hubs solve the
  same problem separately with an alphabetical slug.
- **`popularity_desc` must not filter, only order.** `popularity IS NOT NULL`
  was applied as a WHERE on every search, and only 2.7% of works have a score,
  so "Most popular" silently deleted ~97% of matches — 7 results under Relevance,
  0 under Most popular. It is kept ONLY for an unfiltered browse, where it is a
  top-N walk of the partial index instead of a sort of 20M rows; any narrowed
  search (`_narrowed`) uses `nullslast()` instead.
- **Truncated titles are hidden from search, not repaired at read time.** The AO3
  dump ships titles cut mid-phrase ("Riding on Brooms With") and 688k rows are
  affected. `_BROKEN_TITLE_TAIL` excludes them by default;
  `include_broken_titles=true` brings them back so nothing is unreachable.
  `ao3_title_repair.py` is the real fix and the worker runs it, but at one AO3
  request per work it will not catch up. Only closed-class words are matched —
  "Sobrevivientes Tercera" is also truncated and deliberately NOT caught, because
  a rule that catches it would hide real titles.
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
- **A dependency's `Response` headers are dropped by endpoints that return a
  `Response`.** FastAPI merges the injected `Response` into the reply only when
  the path operation returns a value to serialise; return a `Response` object and
  it becomes the reply wholesale (`response = raw_response` in `fastapi/routing.py`).
  `get_current_user` sets the rolled-forward session cookie and `Cache-Control:
  private, no-store` there, so both silently vanished on `/api/stories/{id}.epub`.
  Nothing errors and nothing logs — the session just stops extending itself, and
  because `last_used` was already written, nothing retries for 15 minutes.
  `reissue_session_cookie_middleware` now applies it to the finished response;
  don't go back to relying on the injected `Response` alone.
- **The session cookie sends `Expires` AND `Max-Age`, deliberately.** A client
  that ignores one of them does not fall back to a long-lived cookie — it falls
  back to a session cookie, dropped on browser close, which is indistinguishable
  from "stay signed in didn't work" and shows up in one browser only. Whatever
  you change there, the unticked-box path must send NEITHER attribute.
- **A 500 with nothing in the API log is a proxy failure, not an app failure.**
  Check `docker logs <web-colour>` for `socket hang up`/`ECONNRESET`: the request
  died between Next and nginx and never reached uvicorn. It used to hit the first
  request after any quiet spell (an idle night, so the first click of the
  morning). Fixed by `keepalive_timeout 0` on nginx's internal `:8081` listener —
  see the comment there before re-enabling pooling on that hop.
  - **`ECONNRESET` has a second, unrelated cause: the handler simply took longer
    than nginx's `proxy_read_timeout 60s`.** Same log line, same 500, nothing in
    the API log — but the fix is in the endpoint, not the proxy. This is what
    made `POST /api/library/autopoll` the ONLY source of 500s on the public site
    (17 in 72h) long after the keepalive fix: it awaited an AO3 round trip
    inside the request, and that work is bounded by nothing a browser will wait
    for — 3 retries at a 40s read timeout, more than one base host, plus
    `ao3_budget` sleeps that reach a 15-minute cooldown under throttling. It now
    claims its window and returns in ~20ms, doing the poll in a background task.
    Distinguish the two by whether the endpoint does outbound network I/O: if it
    does, suspect the timeout before touching nginx.
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
- **Operator values in the search bar are single-token for the enumerated ones.**
  `_SINGLE_TOKEN` in `query_parser.py` and its twin in `frontend/lib/queryParser.ts`.
  A bare value otherwise runs to the next operator key, which is right for
  `fandom: Harry Potter` and was silently wrong for every fixed-vocabulary
  operator: `rating:M harry potter` took "M harry potter" as the rating, so no
  rating matched AND no search text was left — the bar searched the whole index.
  Same for `site:`, `status:`, `updated:`, `words:`. `language` is deliberately
  NOT in the set ("Bahasa Indonesia" is a real value).
- **The two query parsers must agree.** `backend/query_parser.py` and
  `frontend/lib/queryParser.ts` both parse the same string — the bar to render
  chips and build the URL, the API when it re-parses on the way in. They had
  drifted: the frontend never stripped trailing shorthand, so
  `fandom:Harry Potter complete >100k` (the README's headline syntax) set the
  fandom to the whole string and matched nothing when typed, while the same
  string sent to the API worked. `frontend/lib/queryParser.test.ts` asserts the
  same cases as `backend/tests/test_query_parser.py`; keep them mirrored.
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
