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
- **The language filter seq-scanned 20.5M rows past an index that could have
  answered it in a millisecond.** `ix_stories_language` is a plain btree and the
  predicate was `ILIKE`, which cannot use one. Measured, same row, same box:

      WHERE language ILIKE 'Welsh'   18,510 ms   parallel seq scan
      WHERE language  =    'Welsh'        1 ms   index scan

  The index had been sitting at ZERO scans and 258MB — not the wrong index, just
  never asked a question it could answer. Equality is safe because
  `language_variants()` returns the spellings AS STORED (`中文-普通话 國語`, not a
  pattern), which is the entire point of the alias table; the no-variant
  fallback tries the obvious casings instead of reaching for a pattern.
  - It was wrong as well as slow. `language=Welsh` returned NOTHING while
    `language=Cymraeg` returned 93, because the endonym is what is stored and
    Welsh was not in the table. Counted against the data: 63 languages have over
    200 works and seven were unnameable, 2,815 works reachable only by typing an
    endonym exactly. Added, with Welsh and two more of AO3's Chinese topolects.
  - **The zero-scan column in `pg_stat_user_indexes` is a lead, not a verdict.**
    The same list shows `ix_stories_characters_trgm` (2.1GB) and
    `ix_stories_fandoms_trgm` (1.5GB) at zero scans, and dropping their two
    siblings on that reasoning once cost 83 seconds a query — they are the
    last-resort fallback and their being unused is the design working. One entry
    on that list was a bug and two are load-bearing; the column tells you where
    to look and nothing more.
- **The site was slow every five minutes, and it was the watchdog.** `watchdog.sh`
  runs from cron every 5 minutes and checked the worker's liveness with
  `SELECT max(crawled_at) FROM stories`. There is no index on that column, so it
  was a parallel sequential scan of 20.5M rows — measured at **15.7 seconds** —
  three minutes out of every fifteen spent scanning the biggest table on a home
  server that is also trying to answer searches. A cold `tags=Fluff` browse
  landing in that window took 16s, and one 503'd at the 20s statement timeout.
  - The watchdog never needed the maximum. It needed to know whether ANYTHING
    had been crawled recently, and that question stops at the first matching
    row: 19 buffers and instant while the worker is healthy, because a working
    worker leaves recent rows everywhere. The exact `max()` is still there for
    when the cheap check comes back empty — which is when the worker really is
    stale, when the message wants a number, and when nothing else is competing
    for the disk anyway.
  - Measured after: `tags=Fluff` 16.4s -> 2.5s, `tags=Angst` 15.5s -> 1.7s.
  - **The lesson generalises and this file already had two instances of it.**
    `api/stats.py` guards its `GROUP BY site` scan with a cache, a threading
    lock and an advisory lock; `api/admin.py` had a second copy of the same
    query with none of them and a docstring claiming it came "from the planner
    rather than a scan" — nine seconds, twice concurrently, caught in
    `pg_stat_activity` during the same investigation. When a search is
    inexplicably slow, look for what ELSE is touching `stories`: the answer has
    twice now been a periodic full scan that nobody thought of as a query.
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
- **Half the origin's traffic was one crawler and one number, and both are now
  gone.** Measured over 24h of nginx logs, 85,435 requests: `meta-webindexer`
  22,287 (26%) and `/api/stats/totals` 20,494 (24%). After the two fixes below,
  both read ZERO at the origin.
  - **meta-webindexer is blocked at the EDGE, not in robots.txt, because it has
    never fetched robots.txt** — zero times in the 24h window, while Yandex,
    Semrush and Googlebot all did. You cannot decline a request that is never
    made. 14,187 of its 22,287 requests were into `/?…`, the search space every
    other agent is asked to stay out of because each URL is a query over 20.5M
    rows. `deploy/cloudflare_bot_rule.py` owns the rule; it read-modify-writes
    the phase entrypoint so a rule added by hand survives, and `--remove`
    reverses it.
    - nginx would also work and would still carry all 22,287 down a domestic
      connection through the tunnel before dropping them. The edge is the only
      layer that saves the bandwidth as well as the query.
    - **`facebookexternalhit` is deliberately NOT blocked** (~125/day). It is
      the link-preview fetcher, and it runs when a reader shares a ficatlas link
      on Facebook, Instagram or WhatsApp — blocking it turns those shares into a
      grey box, which is the opposite of what this rule is for. The expression
      matches the product token `meta-webindexer` only, which Meta sends inside
      five different browser-shaped user agents.
    - Verified after deploy: meta-webindexer 403, and facebookexternalhit,
      Applebot, Amzn-SearchBot, Googlebot and an ordinary iPhone all 200.
  - **`/api/stats/totals` is edge-cached**, which needed both halves: a
    `max-age=300` from `total_stats()` and a cache rule to let Cloudflare honour
    it (`respect_origin`, so the header is the setting). 300s because that is
    already both the server's recompute cycle and the client TTL in
    `lib/api.ts` — three layers that were each caching for five minutes while
    the one in the middle was not allowed to.
  - **The audit that found these was of the nginx log, not the traffic table.**
    `visit_events` cannot see any of it: bots are filtered by a user-agent
    substring match, `/api/*` is not recorded at all, and the request that
    matters most here — a crawler walking `/?…` — is exactly the shape the
    beacon never fires for. When the question is "what is this box actually
    doing", the access log is the only source that knows.
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
- **The sitemap told Google half the site changed every day, and the intent to
  avoid exactly that was already written down.** `content_at` is the sitemap's
  `<lastmod>` and moved when `top_ids`, `work_count` or `name` changed. The
  crawler indexes ~15,000 works a day, so `work_count` moves for almost every
  popular hub daily — measured 2026-09-08, **3,185 of 6,165 ship hubs stamped
  that day and 963 the day before**. Google is explicit that it uses lastmod
  only when it is consistently accurate; a file claiming 52% daily churn is the
  same signal as stamping now() on everything, which the comment there set out
  to avoid.
  - Now: the name on its own, the FIRST 20 `top_ids` rather than all of them, or
    a work_count move of more than 1% (floor 10, so small hubs stay honest).
    Going from 52,120 works to 52,121 is not a reason to re-fetch a page.
  - Measured after: a full rebuild of all 6,165 ship hubs bumped 23.
  - Context for why this matters more than it looks: Googlebot fetched ~40 URLs
    a day against 11,196 in the sitemap, and the biggest ship hub on the site —
    `draco-malfoy-harry-potter`, 52,121 works, sitemap position 3, correct
    title, canonical and description, "drarry" 21 times on the page — was not in
    the index at all while two hubs at positions 854 and 2,281 were. Nothing is
    wrong with the page; it has never been fetched. When crawl budget is that
    scarce, every misleading signal costs pages.
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
  - **It takes hours and it deadlocks with the worker, so run it detached.**
    Measured 2026-09-04 on 2.4M rows: attempt 1 deadlocked with the worker at
    the 1h25m mark, attempt 2 took a further 3h45m, and the pass wrote
    2,402,123 rows — coverage 2.68% -> 11.71%. Two operational consequences.
    The docstring's "weekly, rewrites ~500k rows" is five times out. And the
    process must not be tied to a shell that can be reaped: a first attempt
    launched with `nohup docker exec … &` was killed with its shell after
    1h25m and rolled the whole transaction back, silently, with the count
    unchanged. `docker exec -d ficatlas-backend-1 sh -c "python -u
    popularity_rank.py > /tmp/popularity.log 2>&1"` survives.
  - **Nothing after the write may assume `statement_timeout = 0`.** The pass
    sets it at the top, but by the time the write has committed it is no longer
    in force, and the evidence recorder's 20M-row count of the eligible
    population died at 60s — so a pass that had just spent 3h45m writing 2.4M
    rows recorded no evidence that it had happened. The count is now taken off
    `pr_scored` at the top of the run, where it is cheap and the timeout is
    still off, and passed down.
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
    Wiring it in as a fallback where raw engagement is null was measured on
    2026-09-07 and is a bad idea: see the browse-ordering note below for why a
    percentile saturates where a relevance score needs to discriminate. It IS
    now what orders a browse with no query text.
- **A client-rendered route has NO metadata of its own, and here that meant no
  canonical either.** A client component cannot export `metadata`, and the root
  layout deliberately sets no canonical (see the note in `layout.tsx` — a
  canonical there applied to every page that did not override it and made every
  story page a duplicate of the home page). The two decisions compose into a
  hole: `/permissions`, `/takedown`, `/takedowns`, `/permissions/manage`,
  `/follows` and `/forgot` all went out carrying the HOME PAGE's title and
  description with no canonical at all, which is exactly what Search Console
  reports as "duplicate without user-selected canonical" — and none of them
  were indexed. `/series/<uuid>` had the same hole with its own title, across
  329,063 pages.
  - The fix per route depends on what the route is FOR. A page worth finding
    gets a server wrapper that exports metadata and renders the client
    component (`permissions/page.tsx` → `PermissionsClient.tsx`, the same shape
    the story and series pages already used). A page nobody should arrive at
    from a search engine gets a `layout.tsx` with `robots: { index: false }`.
    A route that only redirects should not be a client component at all —
    `/takedowns` and `/permissions/manage` rendered "Taking you to…" and called
    `router.replace` in an effect, so each answered 200 with a real page's worth
    of nothing; both are now server `permanentRedirect`s.
  - **noindex, not a robots.txt Disallow, for anything already in the index.**
    Blocking the crawl stops the page being fetched, which stops anyone reading
    the noindex — a URL Google already knows can sit there indefinitely on the
    strength of its links. Disallow is for spaces that must never be walked (the
    search URL space); noindex is for pages that must come OUT.
  - **A layout's metadata is inherited by every route beneath it**, which is why
    the canonical for `/permissions` is on its page and not on a layout it
    shares with `/permissions/manage`. Same trap as the root layout, one level
    down.
  - `generateMetadata` returning `{}` on a failed lookup has the same effect as
    a client route: generic title, no canonical. `story/[id]` and `series/[id]`
    now name their own URL on every path out, including the timeout path — an
    8s API timeout under crawl load was otherwise enough to manufacture a
    canonical-less duplicate that would be reported long after the request
    recovered.
- **"Complete" was an AO3-only filter for two years because an importer read
  four columns out of nineteen.** 5,222,665 of 6.57M FF.net rows carried status
  `unknown` — honestly, since the HuggingFace dump they came from has eight
  columns and completion is not among them — so a reader filtering for finished
  works got an all-but-AO3 result set. The archive.org SQLite dump has had the
  answer since 2019: its `Status` column is fully populated, 4,191,239
  `Completed` against 4,356,805 `In-Progress` and 79 blanks.
  `ffnet_meta_sqlite_importer.py` was written to fill genres and dates and
  selected exactly those columns; the docstring listed `Status` among the
  nineteen the whole time.
  - **Only ever UP to complete.** "Complete" is monotonic — a work finished
    before 2019 is still finished — while "in progress" is not, and writing it
    from a six-year-old snapshot would state as fact something the source can no
    longer support. That is the same rule `live_fetch/persist.py` follows and
    the same reason the HuggingFace importer records `unknown` rather than
    guessing.
  - The file is 7.2GB and is NOT kept on disk (`backend/data/` is gitignored and
    was cleared). Re-fetch from archive.org/details/fanfic-meta-sqlite as
    `metadata-full.sqlite`, and point `FFN_SQLITE` at it — the default path
    (`/data/ffnmeta.sqlite`) is from an older layout and does not exist in the
    container.
  - **Run 2026-09-07: 8,548,123 source rows read, 5,943,742 of ours updated, and
    FF.net `complete` went 1,346,276 -> 3,646,083.** Completion coverage for the
    archive went from 19.7% to 55.5%; `unknown` fell from 5,222,665 to
    2,922,858. Nearly three hours, and it is a long chain of small batched
    UPDATEs rather than one enormous one, so unlike `popularity_rank.py` it does
    not hold locks the worker needs — the site stayed at 45ms throughout.
  - Run it DETACHED, and then do not restart the backend. `docker exec -d`
    survives the shell that launched it but not `docker compose restart
    backend`, which killed the first attempt at 175,000 rows. `--skip N` resumes
    (the source scan order is stable), which is what it is for.
  - The frontend quotes these figures in two places — `FIELD_COVERAGE.status`
    and the table in `statusNote`'s docstring — and both were written from a
    measurement. Re-measure them after any run of this.
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
- **Being RECOMMENDED is a different measurement from being read, and the index
  could only ever make the second one.** `popularity` blends kudos, bookmarks,
  comments and hits. The works a community presses on newcomers are often
  older, longer, plot-driven and on FF.net — exactly where there is least
  engagement data. Measured against r/HPFanfiction's most-linked list (1,462
  works, 2012-2023): 1,131 FF.net / 331 AO3 recommended, 958 in the index, and
  only **626 with a popularity score at all**. So 836 of the decade's
  most-recommended HP fanfics could not appear in "Most popular" at any
  position. That is missing data, not a ranking bug, and no reweighting fixes
  it.
  - `reddit_recs_import.py` writes `reddit_recs` + `reddit_refs:1376`, the same
    shape as `dlp_library` / `dlp_stars:`. Matching is by ARCHIVE ID out of the
    URL, never by title — this index holds five works called "Manacled".
  - `RECS_BONUS` (1.5, `SEARCH_RECS_BONUS`) sits between trope_bonus (1.0) and
    ship_bonus (2.5). Flat, not scaled by the count: array containment is an
    index lookup where parsing `reddit_refs:N` is a per-row unnest over every
    candidate. `min_recs=N` is there when the number itself matters.
  - It covers ONE fandom, so it has a switch. Measured A/B on "harry potter"
    and "dark harry": the top result is unchanged either way and positions 2-5
    re-order in favour of recommended works.
  - **504 of the listed works are not in the index at all.** They are a crawl
    target, and the importer reports them rather than inventing rows.
- **A row with no summary is DEMOTED, never hidden.** 57% of the index has no
  summary and it is not a crawl failure — the 12.9M-row AO3 bulk dump has no
  summary field at all. Those same rows carry no engagement figure, so `pop` is
  0 and `text_rank` barely separates them: the order AMONG them was arbitrary,
  and a reader met works they could not judge interleaved with ones they could.
  `_thin()` + `THIN_PENALTY` (0.6, `SEARCH_THIN_PENALTY`) subtract from the
  relevance score, and lead the ordering on the no-query browse where there is
  no sort contract to break. Measured on `fandoms=Naruto`: page 1 went from 1
  of 20 without a summary to 0, page 248 is 18 of 20 and page 250 is 20 of 20,
  with the total unchanged at 5,000.
  - It must stay a subtraction and never a WHERE. The failure to avoid is a
    work becoming unfindable; `exact_bonus` alone is 4.0, so a work titled
    exactly what was typed still wins by a wide margin.
  - It deliberately does NOT touch `updated_desc`, `kudos_desc` and the other
    explicit sorts. Those have a contract — "most recently updated" means that
    and nothing else — so a browse by date still shows thin rows where they
    belong.
- **Truncated titles are hidden from search, not repaired at read time.** The AO3
  dump ships titles cut mid-phrase ("Riding on Brooms With") and 688k rows are
  affected. `_BROKEN_TITLE_TAIL` excludes them by default;
  `include_broken_titles=true` brings them back so nothing is unreachable.
  `ao3_title_repair.py` is the real fix and the worker runs it, but at one AO3
  request per work it will not catch up. Only closed-class words are matched —
  "Sobrevivientes Tercera" is also truncated and deliberately NOT caught, because
  a rule that catches it would hide real titles.
- **The header's full-bleed and the header's clipping guard are the same
  argument from opposite ends, and they have now broken each other twice.** The
  full-bleed rule pulls the header out of its shell with negative margins so its
  background reaches the screen edges; the mobile guard caps its width so its
  contents cannot force it past the viewport. The guard was written as
  `max-width: 100%`, which resolves against the CONTAINING BLOCK — the shell's
  content box — so on a phone it capped the full-bleed back to the shell and the
  header ran [0..342] in a 390px viewport, stopping 48px short with the page
  background showing beside it.
  - It is `calc(100vw - var(--sbw, 0px))` now, which is what the guard always
    meant: never wider than the SCREEN. Same `--sbw` as the margins above, so
    the two cannot disagree about where the edge is.
  - The guard is not redundant, and do not delete it as the "cause": it was
    added because the header's contents forced it to 458px inside a 390px
    viewport and the index button was clipped and unreachable. Verified after
    this change at 320, 360 and 390px across /library, /, /settings and /about —
    nothing clipped, because `min-width: 0` and `flex-wrap` are what actually
    let the row shrink.
  - `.library-shell` was missing from the full-bleed list, so that one page's
    header sat inside a 14px gutter while every other ran edge to edge. Added.
    Any new shell needs adding there too, and the symptom is subtle: nothing
    breaks, it just stops matching.
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
- **`users.last_login` is not "last seen", and reading it as one is off by
  weeks.** It is written when somebody types a password. A remembered session
  then rolls its cookie forward for ninety days without ever touching it again —
  that is the whole point of "stay signed in" — so the column answers "when did
  they last prove they know the password", not "when were they last here".
  Measured: the owner read 2026-08-15 while using the site that minute, because
  the last actual login was twenty-four days earlier. The real signal is
  `max(user_sessions.last_used)`, stamped by every authenticated request (at
  most every 15 minutes; see the reissue note in api/auth.py). Expired sessions
  count too — a lapsed session is still evidence of when its owner was last
  here. The admin People list shows the later of the two, and keeps the login
  date beside it because it answers the other question.
- **A collapsed section on a phone is a section that does not exist.** The
  account list was added to /admin and reported as missing twice, because on a
  narrow screen it was a heading you had to know to tap. The fix was not more
  explaining: the count went into the TILE ROW at the top, where the eye already
  is ("2 Accounts · 1 with no email"), and the section itself now stays open —
  it is four lines, not the three thousand pixels of coverage bars the
  collapsing was built for. Collapse what is long, not what is important.
- **The admin panel's Background jobs section shows EVIDENCE, not heartbeats.**
  Every row is something a loop left behind — a build timestamp, a watermark, a
  log row — rather than something it reported about itself, because a heartbeat
  says "I ran" and evidence says "I achieved something", and the second catches
  a loop running happily over a broken query. `popularity_rank.py` sitting
  frozen for months is the failure this exists to make visible.
  - **Thirteen of the worker's twenty loops had no row at all**, which is the
    exact failure this section exists to prevent, for most of the work the site
    does: enrichment, recent works, the archive walks, the listing harvest,
    title repair and both Wayback pairs. They now share ONE row, because they
    share one piece of evidence — every loop that fetches a work stamps
    `crawled_at`. It cannot say which loop is working; "nothing has been fetched
    for six hours" is the alarm that was missing. The probe is bounded
    (`LIMIT 200` inside a 48h window), never `max(crawled_at)`, which is the
    15.7-second sequential scan that made the whole site slow.
  - The AO3 Wayback queue was missing while its FF.net twin was listed, so the
    larger of the two was invisible: 506,635 against 108,509.
  - **Pick the column carefully; the obvious one is often wrong.** It has now
    happened twice. IndexNow's row read `indexnow_watermark`, which is a CURSOR
    into hub `content_at` and therefore always lags — it reported 45.2h against
    a 36h budget on a day the loop had run 23 hours earlier and submitted 5,534
    URLs to a 200. `indexnow.run()` now records `indexnow_ran_at` on every
    accepted submission, including one that correctly had nothing to send, and
    the row reads that with the watermark only as a fallback. The AO3
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
    - **A search made from the filter panel was not recorded at all**, until
      2026-09-07. The middleware wrote a row `if q`, so every fandom hub, every
      ship hub and every fandom, character or tag clicked on a result card was
      invisible — measured on 24h of origin logs, 22 of 38 searches carried no
      `q`. The report was blindest to the commonest way the site is used, which
      also means any "searches" figure quoted from before that date counts TYPED
      searches only. `serialise_filters()` in `query_parser.py` now renders the
      filters in the SEARCH BAR's syntax (`fandom:Naruto complete`), so the row
      is the text the reader had in front of them and pastes back in to re-run
      the search. It is mirrored by hand against `serializeFiltersToQuery` in
      the frontend, like the two parsers, and
      `tests/test_query_parser.py` asserts every case ROUND TRIPS through
      `parse_query` — which is the property that makes a recorded row usable
      rather than merely readable.
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
- **Paging cost a whole search per page, and one computation now fills five.**
  The work is not in returning twenty rows — it is materialising up to 5,001
  candidates and ranking every one. OFFSET does not avoid that; it does the work
  and throws it away. Measured cold: `coffee shop au` page 1 3.15s, page 2
  2.10s, page 8 2.10s. The set is already sorted by the time rows come back, so
  `SEARCH_PREFETCH_PAGES` fetches `per_page * 5`, returns the first page and
  caches the other four under the keys their own requests will use. After:
  page 2 is 0.018s (`coffee shop au`) and 0.005s (`naruto`). Verified
  byte-identical to a freshly computed page on three query shapes, pages 2 and
  4, including `total` and `count_is_capped`.
  - **The cache key is canonicalised (parameters sorted) and that is what makes
    the prefetch possible** — `key_for_page` has to produce exactly the key the
    next request looks under, not an approximation. It also fixes a plain miss:
    `?q=x&page=2` and `?page=2&q=x` used to be two entries for one search.
  - Prefetched pages are skipped when live cards are present, and never carry
    `live_count` or `hidden_explicit` — those belong to the page actually asked
    for.
- **"Most popular" was AO3 and nothing else, and the per-site percentiles were
  not the fault.** Measured over 2,402,138 scored works: the top 1,000 held
  1,000 AO3 works, 0 FF.net, 0 FictionAlley — while AO3 is only 84% of what is
  scored. The per-metric percentiles were already per-site; it is the BLEND that
  compresses each archive differently (confidence shrinkage, the rate term, each
  site's own `w_max`), and it compresses the small archives hardest — 99.9th
  percentile 0.9973 AO3, 0.9426 FF.net, 0.7999 FictionAlley. No FF.net work
  could reach the band two million AO3 works already occupied. Rescaling the
  endpoints does NOT fix it (simulated: 997/2/1) because the difference is in
  the shape. `popularity` is now `percent_rank()` of the blend, partitioned by
  site, so every archive is uniform on 0..1 and contributes in proportion to how
  much of it is scored: simulated top 1,000 becomes 836 / 151 / 13 against
  populations of 84% / 15% / 1.2%.
  - **Rebuilt 2026-09-06 22:02 and it landed within a percent of the
    simulation.** 2,487,976 works scored against 2,487,978 eligible, on the
    first attempt with no deadlock, in 3h51m. The measured top 1,000 is now
    **842 AO3 / 146 FF.net / 12 FictionAlley** against the simulated
    836 / 151 / 13 — a sort that had been 1,000 / 0 / 0 for as long as it had
    existed. The unfiltered "Most popular" browse opens on a FictionAlley work,
    which is the whole point of the change and is also the cheapest way to check
    it is still in force: `?sort=popularity_desc` with a single-archive top ten
    means the percentiles have been rebuilt without the partition.
- **The search page's keyed remount was VISIBLE, and it showed the reader the
  landing page.** `SearchPageKeyed` keys the component on the query string, so a
  search or a page change destroys the instance holding the results — and the
  replacement came up with `results=null, loading=false`, which is exactly the
  combination that renders the front door. Measured in a browser across one
  click of "Next" on a cold page: results → **landing page** at 92ms → six
  skeletons for 1.5s → results, and the reader left 365px down page 2 because
  the scroll-to-top lived in `doSearch`'s success path and the navigation's own
  unmount had aborted that fetch. The fix is to seed `results`, `loading` and
  `stale` from module scope at mount, so the new instance comes up in the state
  its predecessor was in; a page change keeps the previous rows on screen dimmed
  (`.results--pending`) rather than collapsing the column. Three states, no
  flash, and the reader lands at the top. The duplicate query above is still a
  duplicate query — it is just no longer something anyone can see.
  - `stale` is only ever the SAME search on another page (`sameSearchOtherPage`).
    A different search shows skeletons, because its predecessor's rows are not
    an approximation of the answer, they are a different answer.
- **Scroll memory had three separate bugs, and each one hid the next.** "Back
  from a story returns you to your place in the results" did not work at all;
  every one of these was found by driving a real browser, and none of them
  raised an error anywhere.
  - `restoreScroll` asked `scrollHeight > y` when the question is whether the
    document can REACH y — `scrollHeight - innerHeight >= y`. On a back-
    navigation it runs while the page being LEFT is still laid out, so a 1,882px
    story page passed the test for y=1,400, the scroll was clamped to 982, and
    the entry was then cleared as though it had worked. It now verifies
    `window.scrollY` landed before doing anything irreversible.
  - It cleared the entry on success, which lost the position whenever the reader
    was ALREADY at that height: scrolling to where you already are fires no
    scroll event, so the page's capture never wrote it back. The entry is now
    the last known position for that URL and is only removed deliberately.
  - The capture recorded `y=0` from the ROUTER's scroll-to-top on navigation.
    The guard for that tests `location.pathname === "/"`, and measured, the
    router's scroll can arrive while the pathname is still `/` — so the reader's
    position was overwritten with 0 one frame before the unmount flush read it,
    and clicking a story within 150ms of scrolling lost it every time. Only a
    `y > 0` is remembered for the flush; a deliberate return to the top is
    distinguished by surviving the 150ms debounce.
  - The unmount cleanup now writes the pending position, which the comment above
    it had claimed since it was written — `scrollMemoRef` was read by nothing at
    all. `lib/scrollMemory.test.ts` covers the reachability rule, the
    keep-on-success rule and the save suppression; the rest needs a browser.
- **A browse with no query text was ordered by LENGTH, and that is the page most
  readers actually see.** Every fandom hub, every ship hub and every fandom,
  character or tag clicked on a result card lands on a filtered search with no
  `q`, which fell to `_thin ASC, word_count DESC`. Measured on `fandoms=Naruto`:
  the page opened with a 1,066,440-word one-shot collection carrying 125 kudos,
  followed by the four next-longest works, none of them anything a reader would
  name. Length is uncorrelated with anything anybody is choosing between, so
  from the outside it reads exactly as "the results seem random".
  - It is now `_thin ASC, popularity DESC NULLS LAST, word_count DESC`.
    `popularity` only became the right answer recently: it is a per-site
    percentile, so it compares archives rather than ranking by which archive a
    work came from, and the 2026-09-06 rebuild took it from 2.7% of the index to
    12.1% with that partition in force. Nulls last because an unmeasured work is
    not an unpopular one, and length still breaks the tie among those.
  - **Ordering was only half of it: the candidate set was an arbitrary sample.**
    A browse ranks the first 5,001 matches the planner happens to return, which
    on `fandoms=Harry Potter` is 0.4% of the match set — the works everyone
    means were simply not in the room. There is now a best-read arm beside it
    (`BROWSE_POPULAR_CANDIDATES`, `BROWSE_POP_FLOOR`), exactly mirroring the
    kudos arm the text path has had: additive, so it can never remove a match,
    and bounded by a percentile floor rather than gated on breadth because a
    browse has no cheap breadth measure. `fandoms=Harry Potter` now opens on
    *All the Young Dudes* (322,055 kudos) with an FF.net and a FictionAlley work
    in the top three; before, the first result was whatever the sample's longest
    work happened to be.
  - The floor makes the arm cheap where it matters and free where it does not:
    a filter whose whole match set fits under the ceiling already has every row
    as a candidate, and UNION dedups. Measured — Harry Potter 21.7ms, Naruto
    435ms cold, a tag matching nothing popular 3.7ms (the planner BitmapAnds
    instead of walking).
  - **`popularity` is the wrong shape for the TEXT relevance score, and the note
    that used to sit here suggesting it should be wired in as a fallback was
    wrong.** It is a percentile, so it saturates exactly where ranking happens:
    measured on `time travel harry potter`, all fifteen top results score
    between 0.997 and 1.000, and substituting it for the raw `ln(1 + kudos +
    hits/20)` term would flatten every distinction among the well-read works
    into a tie broken by text noise. Percentiles are right for ORDERING a browse
    and wrong for WEIGHTING a score. The text path was measured and deliberately
    left alone; its top results are already sound.
- **The reader had three controls, and a 199-chapter work needs four.** Previous,
  All chapters, Next — so reaching chapter 140 meant 139 clicks, and reaching the
  LATEST chapter of a work still being written (the one thing a returning reader
  wants) meant going back to the story page and scrolling a list of two hundred
  links to the bottom. `story.chapters` was already in hand; nothing needed
  fetching. The reader now carries a chapter `<select>` and the story page a
  "Latest chapter →" beside the Chapters heading.
  - A NATIVE select, not a custom menu: the browser brings keyboard typeahead, a
    list that knows how tall the screen is, and a real picker on a phone, all of
    which would otherwise be rebuilt worse by hand.
  - It lists the chapters the reader HAS, which offline is the ones that
    downloaded — the same rule `prevNum`/`nextNum` follow, and for the same
    reason.
  - **The reader's own error screen asked for a sign-in and offered no way to
    do it.** `REQUIRE_LOGIN_TO_READ` is on for the public tier, so on
    ficatlas.com every hosted chapter 401s for a logged-out visitor — correct,
    and the reader says so clearly ("You are not signed in, or that is not yours
    to see") — but the actions beside that message were "← Chapter 4" and "Back
    to story". There is now a "Sign in to read" carrying `?next=` back to the
    chapter, shown only when nobody is signed in: a 403 for somebody who IS
    signed in means the work is not theirs, and offering them a login is telling
    them to try the same key again.
  - Anonymous verification of the reader is therefore impossible against
    production, and the dev tier is not a control for it — dev serves hosted
    chapters to anyone (tailnet-only, single user). Drive the reader against dev
    and check the AUTH path with a routed 401.
- **"Clear N filters" queued instead of clearing, and its own tooltip said
  otherwise.** Sidebar filters deliberately sit behind an Apply bar so that
  building a filter set out of three clicks does not run three searches. Clearing
  is not building: the person who needs that button arrived from a link that
  applied filters they never chose, and the button promised to "remove every
  filter and search the whole index". It set state and waited for a second click
  on a bar elsewhere on the screen.
  - It is now a LINK to the same search with the filter parameters dropped —
    one click, and it cannot go stale the way `doSearch()` after
    `clearFilters()` would (buildParams is captured at render, so that reads the
    filters as they were BEFORE the clear).
  - The onClick stays alongside the href and is load-bearing: filters ticked in
    the sidebar and not yet applied exist only in state, and with no filter keys
    in the address the cleared URL is the one we are already on — a link to
    where you already are does nothing at all.
- **A third of the admin page was off the side of the phone, and nothing could
  scroll to it.** `body` is a flex COLUMN (the page-frame rule that pins the
  footer), and `margin: 0 auto` on a flex item sets auto margins on the CROSS
  axis — which switches off `align-items: stretch` and leaves the item sized to
  its own content, capped only by `max-width`. So `.settings-shell` stopped
  being "720px at most" and became "720px", on a 390px screen, with
  `html, body { overflow-x: clip }` hiding the 330px that fell off. Measured on
  /admin?tab=traffic: shell 720 inside a body of 390, `body.scrollWidth` 720
  and no scrollbar, because clip is explicitly not a scroll container.
  - Every centred shell now carries `width: 100%` so max-width caps rather than
    sets. `.settings-shell`, `.reader-shell`, `.page-prose`.
  - **It only bit when content was wide enough to push**, which is why every
    audit of an empty or narrow page said the layout was fine. The trigger was
    `.traffic-table td { white-space: nowrap }` (0,1,1) beating
    `.traffic-table__q { white-space: normal }` (0,1,0) — so the one column the
    comment calls "allowed to be long" was the only one that could not wrap, and
    a query cell measured 1,412px. Test a page at a phone width with REAL data
    in it; empty tables cannot overflow.
  - `overflow-x: clip` on body is doing its job and is not the bug. It is what
    keeps a stray wide element from making the whole site scroll sideways. It
    also means a layout fault is silent, so an audit that only asks "does the
    document scroll horizontally" will always say no. Ask instead whether any
    child of body is wider than body.
- **The traffic tables sort, filter and copy now, client-side.** The rows are
  already fetched — a couple of hundred at most — so "which searches found
  nothing", "which page has the worst visitors-per-view" and "when did that
  referrer last send anybody" are a comparator, not a round trip. Sorting on the
  server would need a parameter per column per endpoint, a re-fetch per click,
  and would still be capped by the same limit.
  - Nulls sort last in BOTH directions. `results: null` means no exit recorded a
    count, not "found zero", so ascending must not open with rows that have no
    value at all — the same rule as `nullslast()` in api/search.py.
  - Copy writes TSV and has TWO implementations, because the admin page is
    opened over both http and https: `navigator.clipboard` does not exist
    outside a secure context, and the dev host is plain http over the tailnet,
    so on the machine this site is actually administered from the modern API is
    undefined and the button did nothing, silently. The textarea +
    `execCommand` fallback is deprecated and works everywhere. The button says
    which happened rather than claiming a success it did not have.
  - **Sticky `thead` and `display: block; overflow-x: auto` on the same table
    are mutually exclusive.** The block+overflow makes the table its own scroll
    container, so the heading sticks to a box that never scrolls vertically and
    slides away with the rows (measured at top:-951). Once the query column
    could wrap the table had nothing to scroll, so the scroll container went and
    the headings stayed. Same trap as the reader-toolbar note above.
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
