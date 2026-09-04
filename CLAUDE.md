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
- **SQLAlchemy's pool is per PROCESS, so every pool size multiplies by
  `WEB_CONCURRENCY`.** Against a server-wide `max_connections = 100`, the
  configured maxima were: dev backend 4x(16+8)=96, worker 12+6=18, public api
  2x(24+12)=72 — 186, and 258 while a promote has both colours up. Pools are
  lazy so measured use is ~43 and it has never been hit, but `api/stats.py`
  records the outage shape when it is: "QueuePool limit of size 12 overflow 6
  reached" and every request 500s, searches included. The dev backend is now
  6+3 (=36) because it serves one person over the tailnet while sharing a
  ceiling with the public site. The public tier is still 72 and is the
  remaining large consumer — shrink it only with a measurement, and remember a
  search may now hold 64MB of `work_mem`, so more connections is not free.
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
- **robots.txt must have no blank line inside a user-agent group.** RFC 9309
  (2022) ends a group at the next `User-agent:` line; the 1994 draft it replaced
  ended it at a blank line, and parsers written to the older reading are still
  common. A blank line sat directly under `User-agent: *`, so to any of them the
  wildcard group produced **zero** rules — no `Disallow: /*?`, no private routes,
  no `Crawl-delay`. Google, Bing and Amazon read it correctly throughout
  (Amzn-SearchBot arrives once every 10.0s, pinned to the Crawl-delay), so
  nothing looked wrong. Comments do the spacing instead; blank lines BETWEEN
  groups are correct and required. `python3 tests/check-robots.py` enforces it
  and re-parses the file with the strict parser to check the rules still land.
  - `*` and `$` in a path are RFC 9309 extensions with no 1994 equivalent, so
    `Disallow: /*?` cannot be made to work for a legacy parser however it is
    written — the checker asserts those two rules are present rather than
    effective. The residual gap is that hub pages carry ~157 un-nofollowed links
    into `/?…`; it is theoretical today (over a full day, every request to `/?…`
    came from a browser or this project's own scanner, none from any crawler).
- **SEO-audit crawlers were 18% of story-page load and sent nobody.** SemrushBot
  made 7,287 requests in one day, 7,024 of them story pages, against Googlebot's
  52 requests in the same day. It reads robots.txt (70 fetches that day) and is
  now disallowed, along with Ahrefs/MJ12/DotBot/DataForSeo/BLEXBot/Barkrowler/
  Seekport named pre-emptively. Applebot (25,597/day) and Amzn-SearchBot
  (8,940/day) are deliberately NOT blocked — they have a search product behind
  them, so their load buys discovery.
- **There are two hub tables, built by one module.** `fandom_hubs` (5,025 rows,
  one per fandom) and `ship_hubs` (6,165 rows, one per romantic pairing) have an
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
  - **The loop was not enough, because it left no evidence.** Measured
    2026-09-04: 549,515 works scored against **2,399,048** carrying an
    engagement figure — the same number as before the loop was written, while
    the eligible population had more than doubled as the crawler enriched rows.
    "Most popular" was covering 2.7% of the index where the data supports 11.7%.
    Its only trace was one line in a 75,000-line worker log, and the admin
    panel — which exists *because this script once sat frozen* — had no row for
    it. It now records `popularity_built_at`, `popularity_scored` and
    `popularity_eligible` into `app_settings` on every successful pass, and the
    panel shows both the timestamp and the backlog. Two numbers, not one: a
    pass that runs on time and falls further behind every week is
    indistinguishable from a healthy one by timestamp alone, and that is the
    failure that actually happened.
  - **11.7% is the ceiling, and it is a data ceiling, not a bug.** 88% of the
    index has no engagement figure at all and none can be imported: the
    HuggingFace FF.net dump has eight columns (source_file, category, rating,
    chapters, words, story_url, summary, language), the archive.org SQLite dump
    has nineteen, and neither carries favourites, follows or reviews; the AO3
    bulk metadata dump carries id, title and metadata only. Coverage grows only
    as the crawler enriches rows. Do not go looking for a column to widen the
    eligibility predicate with — `favourites` is present, unused and 0 on every
    row in the index.
  - Consequently a FF.net work almost never wins a relevance sort: only 6.1% of
    FF.net rows carry any engagement number (AO3: 13.9%), and the `pop` term in
    `api/search.py` is raw `ln(1 + kudos + hits/20)`, which is 0 for the rest.
    `popularity` — the percentile that exists precisely to make the archives
    comparable — is a SORT option and is not a term in the relevance score.
    Wiring it in as a fallback where raw engagement is null is a real change and
    has not been measured.
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
- **A 530 with nothing in ANY log on this box is the tunnel, and it was QUIC.**
  Cloudflare 530 means the edge could not reach the origin at all, so nothing
  gets as far as nginx and no log here records it — the only place it is visible
  is Cloudflare's own analytics, where it was 2,182 requests in thirty days
  (1,470 of them in one day). Cause: cloudflared's default QUIC transport over a
  home connection, dropping and re-registering ~20 times an hour — 698
  "Connection terminated" events in 72 hours, and "timeout: no recent network
  activity" arriving on all four connections at once, which is the UDP path
  going dead rather than anything cloudflared did. `--protocol http2` in
  `docker-compose.public.yml` moves it to TCP; after the switch cloudflared's own
  precheck reports `suggested_protocol=http2`. Recreate the container to apply
  (it shares nginx's netns). This matters beyond uptime: 5xx is the signal a
  search engine answers by crawling less.
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
- **The admin panel's Background jobs section shows EVIDENCE, not heartbeats.**
  Every row is something a loop left behind — a build timestamp, a watermark, a
  log row — rather than something it reported about itself, because a heartbeat
  says "I ran" and evidence says "I achieved something", and the second catches
  a loop running happily over a broken query. `popularity_rank.py` sitting
  frozen for months is the failure this exists to make visible.
  - **Pick the column carefully; the obvious one is often wrong.** The AO3
    stale-WIP refresh was added with `max(queued_at)` on `ao3_refresh_queue`
    and flagged STALE on the very first render — while the loop was running
    every 40 minutes and had logged a pass four minutes earlier. The queue is
    refilled in batches and drained a few at a time, so all 160 rows carry one
    timestamp from the last refill. It is now excluded, with the reason written
    at the exclusion. A panel that cries wolf is worse than no panel.
  - Growth (works/day) is SAMPLED into `app_settings.admin_growth_samples`, not
    queried. `GROUP BY indexed_at` over `stories` has no index and measured
    **15.7 seconds**; an index for it would cost ~600MB on a disk at 71%.
  - Storage bars scale to the largest object, not the database total — `stories`
    is 92% of 39GB, so against the total every other bar is a stub.
- Anonymous traffic lives in `backend/tracking.py` (buffered writer, daily-rotating
  keyed visitor hash, 90-day retention) + `backend/api/traffic.py` (public
  `POST /hit` beacon, owner-only reports) + the Traffic tab on `/admin`.
  - **The search log is NOT a clean record of what readers type, and it is
    quoted as evidence in code comments — treat those numbers with suspicion.**
    Two separate defects, measured 2026-09-04 over 1,370 recorded searches:
    - **Paging counted as searching.** Every results page is a second
      `/api/search` request carrying the same `q`, and `path` recorded the bare
      `/api/search` either way, so one reader working through a long result set
      wrote a dozen identical rows. `main.py` now puts `?page=N` in the path for
      N>1 and the report counts first pages only, so "runs" means searches.
      Rows written before that change cannot be told apart retrospectively.
    - **`is_bot` is a user-agent substring match and says so in its own
      comment.** 18 visitors accounted for 759 of the 1,370 searches and
      produced ZERO pageviews between them — pageviews come from the browser
      beacon, so a search with no pageview is a client that is not a browser.
      One of them ran `wolfstar"; drop table--`, `aaaaa…`, `x and x` and
      `a very narrow specific phrase xyz`: a developer test session, not
      flagged, because its user agent looked like a browser. The report now
      exposes that share as `search_only`.
    - What survives the scrutiny: the 474 `Bts jin and jimin` searches are
      probably real. 460 came from visitors who also opened 5-21 distinct story
      pages, and their inter-search gaps are heavy-tailed (median ~30s, mean
      ~130s, max ~1 hour, standard deviation 3-5x the mean) — the signature of
      somebody reading between searches. The one exception is a visitor whose
      39 searches were 7-31s apart with a standard deviation of 7, which is a
      machine.
    - So `_spelled_out_pair`'s "460 of the 588 searches this site has recorded
      are this shape" is inflated by paging and by synthetic traffic. The
      DESIGN conclusion it supports still holds — the result-count gap between
      `Bts taejin jealousy` and `Bts jin and taehyung jealousy` is a property of
      the index, not of the traffic — but do not re-quote the figure.
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
- **An operator value runs to the end of the text, so mixed queries are split
  back apart in the API, not the parser.** `fandom:Harry Potter time travel` is
  read as ONE fandom of that name — it has to be, because the same rule is what
  makes `tag:slow burn` and `author:Some Long Pen Name` work, and nothing
  syntactic separates them. Only the vocabulary can, and the parser
  deliberately has none (it is mirrored in TypeScript and must stay pure). So
  `_resolve_or_split` in `api/search.py` trims words off a facet value until the
  facets table recognises it and hands the rest back to `q`. Before it:
  `fandom:Harry Potter time travel` 0 results, `fandom:Naruto time travel` 1
  result, `time travel fandom:Harry Potter` 5,000 — the same search, correct
  only when the operator came last.
  - It runs BEFORE anything reads `q`: ship resolution, the category test and
    the FTS predicate all need the recovered text.
  - **The two probes are different on purpose.** "Does the whole value resolve?"
    uses `_facet_variants` (substring, the same umbrella resolution the filter
    uses), so anything the filter can work with is left alone. "Where do I cut?"
    uses `_facet_exact`, and must: substring matching says yes to almost any
    short prefix, so `fandom:Some Fandom Nobody Has` was being cut to
    `fandom:Some` plus three words of text — a confident wrong answer replacing
    an honest empty one. Exact is also cached and a btree hit, so the ordinary
    `fandom:Harry Potter` costs 0.0ms and only an unrecognised value pays the
    trigram scan.
  - A value the vocabulary does not hold verbatim (`fandom:MCU time travel`)
    still will not split. That is today's behaviour, so nothing regresses.
- **The two query parsers must agree.** `backend/query_parser.py` and
  `frontend/lib/queryParser.ts` both parse the same string — the bar to render
  chips and build the URL, the API when it re-parses on the way in. They had
  drifted: the frontend never stripped trailing shorthand, so
  `fandom:Harry Potter complete >100k` (the README's headline syntax) set the
  fandom to the whole string and matched nothing when typed, while the same
  string sent to the API worked. `frontend/lib/queryParser.test.ts` asserts the
  same cases as `backend/tests/test_query_parser.py`; keep them mirrored.
- **Reddit-shaped queries are handled in `query_intent.py`, NOT in the parsers.**
  `websearch_to_tsquery` ANDs every term, so each word of request framing is a
  hard filter over 20M rows: `drarry` returned the 5,000 ceiling and
  `long drarry fics` returned 68 — two words carrying no information about any
  story deleting 98.6% of the answer, and looking to the reader like a thin
  index rather than an error. `looking for a fic where harry raises teddy`
  returned **2**; `recs for slow burn destiel` returned **0**. Three passes, in
  this order, and the order is load-bearing:
  - **Framing out** ("looking for", "fics where", "recs", a trailing "please").
    Dropping a term from an AND-query can only WIDEN, so this needs no gate.
  - **Qualifiers into filters** — "long" is `word_count >= 50k`, not a word to
    find. This NARROWS, so it is gated on the query being a request at all:
    `The Long Way Home` has no framing and keeps its "long". The register test
    runs on the RAW string, before framing is removed, or `long fics` loses the
    "fics" that made it a request.
  - **The phrase against the tag vocabulary.** This is the half that works in
    every fandom without a per-fandom dictionary: `facets` already holds 1.57M
    freeform tags, so "harry raises teddy" finds `Harry Potter Raises Teddy
    Lupin` and "fics where zuko joins the gaang" finds
    `Zuko Joins The Gaang (Avatar)` by one lookup. Word order does not matter,
    which is the point — `harry slytherin` and `slytherin harry` are the same
    request and the archive files both under `Slytherin Harry Potter`.
  Measured: 71→1,947, 2→643, 0→1,630, 22→501, 116→1,901, 190→1,618, 3→318.
  Overhead 12ms on a cold phrase, 0.05ms warm. `SEARCH_QUERY_INTENT=false`
  removes the whole module from the request; `SEARCH_TROPE_TAGS=false` keeps
  the framing and length reading and drops only the tag branch.
  - **The tag branch only ever WIDENS**, like the ship-alias branch and for the
    same reason: it is inferred from user-written tags. It is OR-ed beside the
    text match, and words the tag did not account for are AND-ed onto it —
    `Time Travel` is 45,960 works and "time travel naruto" must not return the
    44,000 that are not Naruto.
  - **Four guards, each written for a query it broke.** A tag matching the
    reader's words only inside AO3's structural furniture is not a match
    (`Dark Mark (Harry Potter)` for "dark!harry"). A word must start a word in
    the tag, not merely appear in it (`along` matched "long", so
    `the long way home` resolved to `this took way too long to write`). The
    reader's words must be MOST of the tag — coverage ≥ 0.55, which separates
    `Harry Potter Raises Teddy Lupin` (0.6) from `Time Travelling Karl Jacobs`
    (0.5). And a query that names a fandom, ship or character is not a trope:
    "toy story" is a 1,473-work FANDOM, and resolving it to the 46 works tagged
    `Alternate Universe - Toy Story Fusion` replaced the fandom with fanworks
    about it.
  - **The ranking bonus is gated on `is_category`.** Ungated it re-created this
    file's oldest bug in a new place: `all the young dudes` is a 13-work TAG as
    well as the most-read work on the site, and +1.8 for carrying it put two
    0-kudos works (one a translation) above the 322,055-kudos original. Below
    the category line the resolution still widens the search; it just stops
    voting on the order. 1.0, not 1.8, because at 1.8 a 6-kudos work tagged
    `Fake/Pretend Relationship` displaced a 4,005-kudos one on "fake dating
    stucky".
  - **A resolved trope reaches the AO3 half of the index and nothing else, so
    the alias table also rewrites the TEXT.** FF.net's `tags` array holds
    provenance markers (`ffnet_dump`, `hf_meta_2024`) and no freeform tags at
    all, and 85% of AO3 rows have no summary, so for a great many works the
    title is the only text there is. The alias table is therefore
    reader-word → the ARCHIVE's words, best first
    (`wandcrafter` → wandmaker / wandcrafting / wandlore), and those spellings
    are searched beside what the reader typed: "wandcrafter harry" went from 3
    works to 318, including 40 on FF.net and a 6,502-kudos work that the tag
    could never have found. The two highest-kudos matches are rated Explicit
    and still correctly hidden by default.
    - They go in as ONE tsquery (`websearch_to_tsquery(a) || …`), not one `@@`
      predicate per spelling — same disjunction, one GIN lookup instead of
      three over 20M rows, and one `ts_rank` instead of three over the
      candidate set (`_story_tsv_ranked` is four `to_tsvector` calls per row).
      Ranking uses that same combined query, or a work found only through a
      rewritten spelling scores zero on text and gets ordered by accident.
    - **An alias firing is itself evidence of a category query** — nobody
      titles a work "wandcrafter harry", so a coined word means a KIND of
      story. Without it the query fell to the title weights and a 0-kudos work
      called "Wandcrafting" outranked every real wandmaker fic. Aliases under
      four characters are excluded from that inference: "mod" is real fandom
      shorthand (MoD!Harry) and also the title word of 242 works in this index,
      most of them about Minecraft.
  - The curated alias table is for ONE case only: a reader's word that does not
    appear in the archive's word at all ("wandcrafter" for `Wandmaker`). If the
    reader's words are already inside the tag, the vocabulary finds it and an
    entry would only add a way to be wrong.
  - It is NOT in `query_parser.py` and must not move there: that parser is
    mirrored in TypeScript and must stay pure, and this needs the database.
    Chips still reach the bar, because the UI renders the API's
    `parsed_tokens`, not its own parse.
  - **The framing patterns come from real request titles, not invention.** The
    two long-running HP fic-finder communities on LiveJournal (hpficfinders,
    potterficfinder) put the whole request in the post title, so their subject
    lines are the corpus: "Fic search: Hermione's parents kidnapped a girl"
    (returned 0 — "fic" and "search" were required of every result), "Looking
    for an old Snarry fanfiction" (119 works led by *Dear Old Snakes*, because
    "old" was a search term), "Searching for a specific drarry fic",
    "Help! I'm looking for a deleted Harry/Draco story on AO3". The FORM is not
    fandom-specific; only the nouns inside it are, which is why the pattern list
    is written once and never per fandom. Adding a shape here is cheap — adding
    one the community does not actually use is not.
    - "…on AO3" is the `site:` filter, not a word every result must contain.
    - `old`, `specific`, `deleted`, `lost` describe the REQUEST. They are gated
      on request register like the length words, so `Old Man Logan` survives.
    - `story`, `fic`, `book`, `chapter`, `work` are words for the ARTEFACT and
      are never trope content: a bare "story" left by "a Harry/Draco story on
      AO3" resolved through the stem `stor` to `Storytelling` and
      `Storybrooke`, a 2,120-work tag branch and a category promotion.
  - **Match a word however the archive inflected it.** The archives write one
    trope every way round — `Sirius Black Raises Harry Potter` (236 works),
    `Harry Potter was Raised by Sirius Black` (51), `Sirius raising Harry` — and
    a reader types whichever they think in. `_stem` strips a short, safe suffix
    list before the word-start regex. `er`/`ers` are NOT in it and must not be:
    they turn "traveller" into "travell" and "master" into "mast", which stops
    matching the words they came from. Never below four characters, because a
    three-letter prefix anchored at a word start matches most of the vocabulary.
  - **A partial trope resolution still says what the reader meant, if the rest
    names a thing.** "omegaverse bakugou" is a trope and a character and nothing
    else, so it is a category query — but `_query_is_category` cannot see that:
    it probes exact sub-phrases and deliberately excludes single words, so a
    one-word trope beside one character scored zero, the query fell to the title
    weights, and works with the shorthand in their title and no readers came
    first. `_names_a_thing` on the leftover is what distinguishes that from
    "the long way home", whose leftover is "home".
- Never put a `:param` token inside a `--` comment in a `text()` query.
  SQLAlchemy binds it there too and psycopg2 substitutes into the comment, so any
  value containing a newline escapes into executable SQL. This stalled series
  detection indefinitely (`syntax error at or near "twitter"`, from a Blogger
  share widget scraped as an author name). `tests/test_series_detect_sql.py`
  guards it at source.
- **Relevance ranks over a FIELD-WEIGHTED tsvector, and that vector must never
  be used for matching.** `fic_doc()` flattens title, summary, author and every
  facet into one string — correct for the `@@` predicate (one index, match
  anywhere) and the reason `ts_rank` over it could not rank: on `coffee shop au`
  every plausible answer scored between 0.05 and 0.14, a band narrower than the
  noise between them, so the order inside it was arbitrary. That is why relevance
  "felt random" on any query naming a KIND of story. `_story_tsv_ranked()` uses
  setweight() (title A, tags B, subject facets C, summary/author D) and is
  applied AFTER retrieval to the ~5,650 materialised candidates, so there is no
  second index and no reindex — benchmarked at +0.3% median over five queries.
  `tests/test_search_ranking_tsv.py` asserts the two expressions stay distinct;
  putting setweight into the matching side drops `ix_stories_doc_fts` and
  seq-scans 20M rows.
  - Normalisation flag is **0**, not 1. Flag 1 divides by document length, which
    is a bonus for being short: on `all the young dudes` four 0-kudos works
    scored the maximum because they have almost no text, above the 322,055-kudos
    work of that name. Field weighting solves the problem flag 1 was added for
    (a bare title outranking works tagged with the subject), so the length
    division only contributed the sparse-document bug.
  - **Two weight arrays.** A title query weights the title highest; a CATEGORY
    query weights TAGS above the title, because being tagged with a trope is
    evidence and being named after it is a coincidence. Without that split
    `dramione` returned a 490-kudos drabble collection with the word in its
    title above works with 6,001 and 22,210 actually tagged for the pairing.
  - When the query resolved to a ship, `w_text` drops to 0.3. The pairing is
    already known, and the 1,248 works tagged Hermione Granger/Draco Malfoy do
    not contain the word "dramione" at all — so text can only reward a
    coincidental title.
  - Two approaches were tried first and BOTH measurably regressed, so do not
    reach for them again: Reciprocal Rank Fusion (k=60 is tuned for lists of
    tens, not 5,001 candidates — text ranks in the thousands make the text term
    vanish and it collapses to a popularity sort) and percentile normalisation
    (percentiles are relative to an arbitrary candidate sample, so it surfaced
    400-kudos works and dropped the 322,055-kudos work off `all the young
    dudes`). The score-spread metric that diagnosed the problem did NOT improve
    with the fix that worked — it identified the right subsystem and was the
    wrong thing to optimise.
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
