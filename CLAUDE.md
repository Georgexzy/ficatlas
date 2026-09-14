# Notes for Claude

Quick orientation for an agent working on this repo. FicAtlas is a Dockerized
fanfiction search engine: Next.js 15 frontend (port 3000, reverse-proxies
`/api/*`) + FastAPI backend (8000) + PostgreSQL 16 (~19.7M `stories` rows).
Live tree is `/home/george/ficatlas` (not this worktree).

## Systems audit: what was actually redundant

Audited on request across all systems, not just background jobs. Most of what
looked duplicated was already shared, and the real fault was the opposite —
jobs that existed and nothing ran.

**Already harmonised, checked and left alone:**
- `is_bot` has ONE implementation in `tracking.py`; `main.py` and
  `api/traffic.py` both import it.
- `hub_build.build_groups` is shared by `fandom_hubs` and `ship_hubs` — it
  looked orphaned in a reference scan and is not.
- `admin._site_totals` calls `stats.site_counts_without_scanning()` first and
  only scans on a cold cache, behind the same advisory lock.
- Author opt-outs: `optout_sweep.py` is documented as a ONE-TIME sweep, and new
  imports are covered at ingest by `author_permission.py` calling
  `external_optout.has_external_optout`. Two mechanisms, one policy, no gap.

**The real finding: three jobs were written, committed, and never scheduled.**
`content_gates`, `reddit_recs_import` and `tropedia_recs_import` each existed
only as a command somebody had to remember to type. They now share ONE
`_curation_loop` rather than getting three of their own, because they are one
job in three parts: two sources of community recommendations writing the same
marker, and the gate repair that must follow any bulk change to tags.
- Order matters: the recs imports rewrite `tags`, which fires the content-gate
  trigger per row, so gates are already correct for anything they touched. The
  repair exists for the other case — a change to the TERM LISTS, which no
  trigger can retrofit onto rows written before it.
- One source failing must not stop the others, and must not stop the gate
  repair, which is the safety-relevant half.

**Not redundant, despite appearances:** 10 series-related modules,
6 importers, 4 alias miners. The importers are one-off bulk loads; the series
modules are strategies (`series_from_sequels`, `series_from_summary`,
`series_cues`) composed by `series_detect`, not competing implementations.

## Content safety: two tiers, both default-safe

Everything here exists because its absence did real harm: a reader was **banned
for fourteen days from r/HPFanfiction** for linking a FicAtlas search that
listed works tagged "Underage Sex".

**The principle is not censorship — it is that a URL gets pasted in public and
the person pasting it carries the consequences.** Nothing is removed from the
index. This is an index of what the archives hold, and a reader who deliberately
asks for something the archives themselves label is entitled to find it. What
the site will not do is put it in front of somebody who did not ask, or bake it
into a link they then share.

### Tier 1 — sexualised minors (`include_underage`, default off)

- **Its own parameter, separate from `explicit`.** That toggle is about taste
  and readers leave it on; this is about what a link carries. Verified:
  `explicit=true` returns 0 underage-flagged works on a page where
  `include_underage=true` returns 4.
- **Nothing that GENERATES a link may set it.** `OutreachPanel` strips
  `include_underage`/`explicit` from every link it builds — stripped rather
  than merely not-added, because the query box is free text — and refuses to
  build a reply at all when the box asks for gated content, since the link
  would then differ from the search on screen.

### The backfill is BATCHED, and three runs were lost before it was

Each pass was one unbounded `UPDATE`. `gate_adult` via tags is **1.4M rows**,
so that statement ran for tens of minutes inside a single transaction — and
every interruption rolled the whole thing back and left the gate exactly where
it started. Three runs died that way (the container was restarted under them),
each losing everything it had done, while the belt-and-braces array filter in
`api/search.py` stayed in the hot path because the flags could not be trusted.
`BATCH` was declared for this from the beginning and never wired in.

Batched, an interruption costs the batch; the next run resumes where the last
stopped, because **the predicate IS the progress marker** — a flagged row no
longer matches `NOT gate_adult`. Same argument as `tropedia_recs_import.py`'s
25-page commits and `popularity_rank.py`'s detached run.

Run it on the WORKER, not the backend: `docker exec -d ficatlas-worker-1 sh -c
"cd /app && python -u content_gates.py > /tmp/gates.log 2>&1"`. The backend is
the container that gets restarted to pick up a code change, which is what
killed every previous attempt.

### Tier 2 — adult and deliberately disturbing (`explicit`, default off)

`Smut`, `PWP`, `Dead Dove: Do Not Eat`, `Incest`, `Rape/Non-con` and friends.
**The Explicit toggle filtered on RATING alone**, so a work rated Teen or Not
Rated and tagged `Rape/Non-con Elements` came back on a default search — the
rating is the author's summary judgement, the tags are the specifics, and on
this index the tags are far better populated.

### Both tiers

- **The explicit toggle was OFF and did nothing.** It filters on RATING, and
  every offending work was rated **M** or **Not Rated** — AO3's "Underage" is an
  archive WARNING, orthogonal to the rating, and the two had never been
  connected.
- **Warnings AND tags**, because the same fact is recorded twice and neither
  implies the other: 10,037 works carry the `Underage Sex` archive warning and
  22,968 carry an `Underage Sex - Freeform` tag.
- **Exact tag values, never a substring.** `Underage Drinking` (25,321 works),
  `Underage Smoking` (7,655), `Underage Drug Use` (3,567) and
  `Underage Kissing` (3,686) are not sexualisation of minors, and a `chan%`
  pattern catches `Chance Meetings`. Over-blocking would hide tens of thousands
  of ordinary stories and teach people the filter is broken.
- **Four separate code paths needed it**, and missing any one would have left
  the hole open: the main search `filters`, the fuzzy-title arm (which builds
  its own query and bypasses `filters` entirely), `/api/search/random` (raw SQL,
  and it is on the LANDING page), and the hub work lists in `api/hubs.py` (the
  crawlable surface search engines send strangers to). `api/hubs.py` imports the
  lists from `api/search.py` rather than copying them — two lists of what counts
  as this content would drift, and the one that drifts laxer is the bug.
- **Operators get no exemption by default.** Delisting is a takedown queue an
  operator must see to work it; this is a different question, and the operator
  is the person most likely to paste a search link somewhere public.
- **Hub pages have no toggle at all.** They are static pages a search engine
  hands to a stranger, so the safe default is the only setting they have.
- Measured on the exact search that caused the ban: **9 of 30 flagged works on
  page one, now 0**, with `underage drinking` and `underage smoking` still
  returning normally.
- `SEARCH_FILTER_UNDERAGE=0` is a kill switch for a future instance with a
  deliberate reason. Nothing in the UI exposes it.

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
- **"Reader-recommended" quietly meant "Harry Potter".** `reddit_recs_import.py`
  is right that recommendation is a measurement kudos cannot make, and it
  covered exactly one fandom — 958 works, all HP, out of 20.5M.
  `tropedia_recs_import.py` adds ~900 fandoms from Tropedia's `Fanfic Recs`
  category (a Fandom-hosted fork of TVTropes' content, CC-BY-SA, with a
  documented MediaWiki API). Measured: 907 pages, ~9.4 archive links each, 89%
  of them matching a work already indexed.
  - **tvtropes.org itself is unusable from a server**: even `/robots.txt`
    returns a Cloudflare "Just a moment…" interstitial. Tropedia carries the
    same content and offers an API, which is the difference between reading a
    site as it offers to be read and going round its front door.
  - Only the LINKS are taken, never the prose saying why a work is recommended.
    That a fanfic appears on a list is a fact; the write-up is the wiki's
    copyrighted contribution.
  - **New generic marker `community_recs`**, written by both importers and
    backfilled onto the existing 958, because that is what the UI filters on.
    `reddit_recs` / `reddit_refs:N` stay — the numeric count is real there and a
    wiki rec list is a yes, not a tally.
  - **The importer commits in BATCHES of 25 pages, and this was learned the hard
    way**: the first full run read 50 of 907 pages, was interrupted, and wrote
    nothing at all, because the single commit came after the loop. A ~900-page
    network read will be interrupted; it should cost the pages not yet read.
- **The recs filter must use `recs_only`, never `tags=community_recs`.** A tag
  filter goes through the trigram ILIKE over `fic_arr(tags)`; for a marker
  carried by ~1,000 rows out of 20.5M that times out and the reader is told the
  index is busy. `recs_only` narrows through GIN containment first. Measured:
  **503 against 0.2s.** The same trap sits behind `min_recs`, which is why that
  one already required the marker via `@>` before its per-row unnest.
- **An unfinished operator was being searched for as text.** Found in the
  traffic log, in a real reader's query:

      fandom: fandom:Harry Potter tag:Female Harry Potter complete

  A bare `fandom:` sat in the bar — typed, or left by the syntax helper —
  `parseQuery` kept it in `clean_text` because it had no value, and
  `serializeFiltersToQuery` writes `cleanText + chips`, so it was re-emitted in
  front of the real operator. The search then full-text matched the literal
  string `"fandom:"`: **51 results where there should have been 1,646.** No
  error, no empty page, just a much smaller answer to a question the reader
  thought they had asked — which is why it survived.
  - Both parsers now drop a bare operator, and both check **FIELD_ALIASES**
    rather than matching any `\w+:`. That second half is the whole fix: the
    first attempt used `/^-?\w+:$/`, which also matches `3:`, and turned
    "chapter 3: the return" into "chapter the return". A colon is ordinary
    punctuation everywhere except after a word the parser recognises — caught
    by a test, not by review.
  - `fandom: potter` is NOT this case: the value runs to the next key, so
    "potter" is the fandom. Locked by its own test so the drop cannot widen.
- **A UA regex alone cannot keep test traffic out, and the first cleanup of it
  was too narrow.** Flagging by "five or more DISTINCT searches inside ten
  seconds" caught three visitors and missed four more, because those runs were
  spaced a minute apart. Nine synthetic queries were still being counted as
  readers afterwards.
  - The reliable signal is **searched and never rendered a page**. The search
    page is what fires the pageview beacon, so searches with no pageviews mean
    the page was never open — something called `/api/search` directly. The
    funnel had excluded these since it was written ("a script and not an
    audience"); the headline counts did not, so the same sessions were scripts
    in one tile and an audience two along. Measured: 65 searches across 12
    visitors, all of it testing from this repo.
  - `_NOT_A_BROWSER` applies it at READ time, so it self-corrects — a visitor
    who searches and then loads a page stops matching on the next query instead
    of staying mislabelled for ever. `script_searches` / `script_visitors` are
    returned and rendered, because a number quietly removed from a total is
    indistinguishable from one that was never there.
  - Honest caveat: a reader whose privacy blocker eats the beacon POST looks
    identical to a script here. They contribute no pageviews either way, so the
    only figure this can understate is searches.
  - **One synthetic query was deliberately left in the human numbers.**
    `hsrry potter wandcrafter` from a session that did render pages — almost
    certainly me in a browser, but indistinguishable from a real typo. Flagging
    on query text would catch genuine typos too, and typos are exactly what
    did-you-mean exists to serve; blinding the data to them would cost more than
    one stray row in 733.

- **Hubs and search are two front doors and nothing compared them.** The panel
  could say a hub was viewed and that a story was viewed, and nothing about one
  leading to the other. `/api/traffic/routes` attributes each story-page view to
  the event immediately before it, within 30 minutes. Measured 2026-09-11:

  | came from | opens | people |
  |---|---:|---:|
  | another story page | 85 | 30 |
  | a search | 82 | 26 |
  | **a fandom or pairing hub** | **55** | **45** |
  | the home page | 48 | 22 |
  | straight in | 24 | 24 |

  - **Read the PEOPLE column, not opens.** Hubs reach 45 distinct readers
    against search's 26 off a third of the views — 197 hub views produced 55
    story opens, while 734 searches produced 82. A searcher opens several
    stories in one sitting; a hub brings one new person to one story, which is
    what a page greeting strangers from a search engine should do. The SEO
    surface is out-performing the product on reach.
  - **Per-view, not per-session.** Session counting cannot tell "searched, then
    browsed a hub, then opened a story" from the reverse, and most people who
    open a story here have done both — 6 visitors did both, 26 searched only, 46
    used hubs only.
  - The 30-minute cutoff is load-bearing: a story opened an hour after a hub
    view is a new visit, and crediting the hub would hand it traffic it never
    sent. Locked by a test.
  - Hub pageviews were never at risk from `_NOT_A_BROWSER`: it needs
    `searches > 0 AND pages = 0`, and 290 non-searching readers have pageviews.
    `NavRecorder` is mounted in the root layout, so every route reports itself.

- **The "Did it work?" funnel was not a funnel.** Three tiles read as
  searched → opened a story → went and read it, but the last step counted
  EVERYONE with an outbound click regardless of whether they had searched.
  Measured over 30 days: 6 in the last step, only 4 of whom appeared in the
  second. A reader who arrived on a hub from Google and went straight to the
  archive was being added to a funnel they had never entered, and with different
  data the last tile could have exceeded the one above it.
  - `read_it` is now `outs > 0 AND searches > 0 AND stories > 0`, so each step
    is a genuine subset of the one before, and the tile can show a percentage.
  - Those other readers are not noise — they are the OTHER route, and the one
    the SEO work is building: every page Google sends a reader to is a hub, so
    arriving there and leaving for the archive never touches the search box.
    Reported separately as `read_without_searching` rather than folded in,
    because mixing the two is what made the funnel wrong.
  - `read_it_partial` gates the "only counted since" caveat on whether the
    window actually starts before collection began. Printing it unconditionally
    teaches the reader to discount a figure that is usually complete.
  - `tests/test_traffic.py` seeds one visitor per route and asserts
    `read_it <= opened_a_story <= searched`.
- **`previous.visitors` was visitor-DAYS wearing the word "visitors".** The hash
  is per day, so `count(DISTINCT visitor)` across a 30-day window counts a daily
  regular thirty times. The comment two lines above it already said summing the
  column would be wrong; this was the same mistake spelled with
  `count(DISTINCT)`. Renamed `visitor_days`. Nothing rendered it, which is
  exactly why it was free to be wrong — an unused field that is subtly false is
  a trap for whoever uses it next.

- **The traffic panel was measuring the developer.** `_BOT_RE` named
  `python-requests` and not `python-urllib`, and a test script in this repo used
  urllib's default `Python-urllib/3.12` — so every query it ran counted as a
  human search. Worse, the visitor hash is derived per day from IP and user
  agent, so each RUN of the script also read as a new visitor. Measured: of 971
  non-bot search events, **173 across 3 visitors were automation** — my own
  canary list (`the arithmancer`, `fake dating stucky`, `wolfstar`) and probe
  queries (`aaaaaa…`, `a and b`, `a very narrow specific phrase xyz`) sitting in
  the audience figures.
  - The regex now names the client libraries generally (`python`, `urllib`,
    `aiohttp`, `okhttp`, `go-http-client`, `java/`, `node-fetch`, `axios`,
    `postman`, …) and the automation drivers (`playwright`, `puppeteer`,
    `selenium`, `webdriver`, `cypress`). curl and Playwright were already
    caught — Playwright's default UA says HeadlessChrome.
  - **An absent user agent now counts as automation.** It returned False
    before, which made the laziest possible client the one most likely to be
    counted as a reader.
  - Historical rows were reclassified by BURST SIGNATURE, not by guessing at
    query strings: a visitor issuing five or more DISTINCT searches inside ten
    seconds is a script, and `harry potter` is a query a real person types. 971
    → 798 search events, 77 → 74 visitors. Earlier untagged testing may still
    remain; going forward it is caught at write time.
  - `tests/test_bot_detection.py` asserts six real browser agents stay human.
    That direction fails silently too — an over-eager regex under-reports and
    nothing about the smaller number looks wrong.

- **Nobody can see anybody else's recent searches, and it is worth being able to
  say so.** `RecentSearches` reads `localStorage["ficatlas:recent-searches"]`
  and nothing else; there is no endpoint that serves them. With an account they
  sync through `userdata.py`, where every route filters on
  `UserData.user_id == user.id`. What looks like other people's queries
  reappearing in the traffic panel is automated testing from this repo — curl
  and Playwright runs are recorded as ordinary searches, and each run derives a
  fresh visitor hash, so a single test script reads as several new visitors.
  Treat same-second bursts of identical queries as what they are.

- **`angst` and `fluff` returned a 503 — FIXED, and the diagnosis took three
  attempts.** The two
  most-used tags in fanfiction — 868,737 and 1,130,841 works here — time out
  after 20s. A bare one-word trope was refused a tag branch, so it fell to a
  full-text scan over every matching row. That half is fixed: an EXACT one-word
  tag match now resolves (`whump` 45,105 works and `omegaverse` 9,489 went from
  a text scan to ~2s), and `_names_a_thing` still rejects `harry`, `naruto` and
  `hermione` before any window is tried, which is what the old rule was really
  protecting.
  - **Two wrong fixes before the right one, and the measurements are the
    point.** (1) Swapping the OR for the tag branch alone fixed `fluff`
    (503 → 10.3s) and REGRESSED `hurt comfort` (7.4s → 503). (2) Also skipping
    relevance ranking changed nothing. Profiling the raw SQL is what settled
    it: **containment picks 5,001 candidates in 434ms**, and `ORDER BY
    popularity DESC NULLS LAST` over the match set costs **8.3s** — while the
    index-friendly form (`popularity IS NOT NULL` + plain DESC, matching the
    partial index) costs **33ms**.
  - The actual fix is that a trope browse must skip the TEXT CANDIDATE
    MACHINERY, not just the ranking. A title arm, a fuzzy-title arm and a
    "best read" arm that intersects the match set with `ix_stories_kudos` all
    ran for `angst` — that last one over 868,737 rows — and none of them has
    anything to do for a query that names no work. `if q and q.strip() and not
    _trope_browse` sends it down the filtered-browse path instead.
  - Measured after: **angst 2.7s, fluff 2.5s, hurt comfort 2.7s**, all from
    20s timeouts, with 5/5 of page one genuinely carrying the tag. No
    regressions — `harry potter`, `drarry`, `coffee shop au` and
    `all the young dudes` all unchanged or faster.
  - The test that asserted the old behaviour was updated rather than deleted,
    and records why the original reasoning ("the text search already matches
    every tag containing it") did not survive measurement.
- **Ship nicknames are fine; I tested a typo and drew the wrong conclusion.**
  The claim here used to be that `romoine` proved a coverage ceiling in
  `ship_aliases`. It did not. The tag is **`romione`** — the fic-finder post it
  came from had misspelled it — and every nickname works when spelled right:
  romione 5,000+, tomarry 4,559, snamione 5,000+, hinny 5,000+, bellamione 288,
  linny 225, pansmione 108. Check the vocabulary before blaming the mechanism.
- **A near miss is worse than a miss, and only zero triggered the rescue.**
  `romoine` returns EIGHT works, none about the pairing, so `_did_you_mean`
  never ran and the reader saw a short page of noise. Zero results announce
  themselves; a handful of wrong ones look like an answer. Suggestions now also
  fire when a SHORT query (≤3 words) returns FEW works (≤25).
  - The near-miss path uses a lower similarity floor, 0.30 against 0.35,
    because a transposed letter barely moves trigram similarity: "romoine"
    against "romione" is **0.333**, which is under the empty-path floor. What
    keeps the looser floor honest is `_DYM_MIN_COUNT` — a candidate must be a
    term 200+ works actually carry.
  - **A guard was removed rather than retuned.** An earlier version required
    the candidate to be 10× larger than `total`, which compares different
    things: `sg.count` counts works carrying a TAG, `total` counts what a
    full-text search matched. It rejected good suggestions for arithmetic
    reasons.
  - Neither trigram nor Levenshtein separates `romione` from `Routine` — both
    score 0.333 and edit distance 2. So the panel offers all three candidates
    and the reader picks, rather than the ranker guessing. Verified it does not
    nag: correct spellings and legitimately narrow searches get nothing.

- **Fandom abbreviations are derived, not listed.** Readers type `twd`, not
  "The Walking Dead (TV)", and every term in a search is a requirement — so
  `twd self insert` returned **9 works**: `Self-Insert` resolved perfectly and
  "twd" was left over as a word no story about zombies contains.
  `fandom_aliases.py` mines 927 abbreviations from the 1,500 largest fandoms.
  - **The convention is not one rule, so several candidates are generated per
    fandom.** "the" is load-bearing in `The Walking Dead` → twd (dropping it
    gives `wd`, which nobody types), silent in `Percy Jackson and the
    Olympians` → pjo, and every word counts in `A Song of Ice and Fire` →
    asoiaf. Both halves of an AO3 name are tried, so
    `僕のヒーローアカデミア | … | My Hero Academia` yields **both** `bnha` and
    `mha`, which are the two things readers actually say.
  - **What it cannot reach, and does not guess at:** `spn` for Supernatural and
    `jjk` for Jujutsu Kaisen are NICKNAMES, not initialisms — one word cannot
    produce three letters. Those need a different source.
  - A STOPLIST refuses abbreviations that are ordinary words. `It`, `Us` and
    `She` are real fandoms whose initialisms would hijack any query containing
    them.
  - Applied only when other words remain: a bare "twd" is a fine text search
    (925 works) and the reader may have meant it literally. Measured after:
    `twd self insert` 9 → **74**, `asoiaf time travel` 138 → **1,159**.
  - **A missing table must not poison the caller's transaction.** The lookup is
    wrapped in `db.begin_nested()`, because the table is built offline and
    genuinely does not exist on a fresh install — without the savepoint the
    failed SELECT aborted the whole transaction and every later query in the
    session raised "current transaction is aborted", including a test's own
    teardown. A feature that is merely OFF cannot take the request down.
- **The outreach panel shipped a reply linking to an EMPTY results page.** A
  real post asking for happy Harry/Daphne fics was condensed to a forty-word
  query, matched nothing, and the panel produced "Try this: <link>" anyway —
  the exact thing the posting rules on that same screen call an advert. A reply
  is now built only when a search has been RUN and has FOUND something; the box
  otherwise says so and tells you to narrow or close the tab. Verified the
  search was never at fault: `harry potter daphne greengrass fluff` returns
  1,058 works.
- **A concept is a GROUP of spellings, and using the rarest one told a reader
  their fics did not exist.** Reported by someone who had demonstrably read
  fics with lordship AND magical power AND politics. They were right: **113
  works carry lordship and powerful together, 26 carry all three, 12 of those
  are 150k+**.
  - `resolve_trope_tags` works from windows of the reader's own words, so
    "harry is magically and politically powerful" found
    `Magically Powerful Harry` — **48 works** — and never saw
    `Magically Powerful Harry Potter`, the same concept on **1,719**. Every
    step after inherited that: `_biggest_spelling` chose the best of what it
    was handed, and the probe tested that one spelling, found no co-occurrence,
    and dropped a concept the post had asked for in as many words.
  - Three changes, each needed alone: `_biggest_spelling` matches PREFIXES as
    well as exact values (a tag beginning with another is the same concept
    spelled longer); the bigger spelling joins the concept's GROUP rather than
    only being its label; and the probe ORs within a concept and ANDs between
    them, which is the shape the search itself builds.
  - **A bare natural-language query beats explicit tag operators**: the same
    three concepts as plain words return **120 works against 52**, because the
    search resolves each phrase and ORs every spelling while an operator pins
    exactly one. Worth remembering before reaching for `tag:"…"`.
  - Caution for the prefix rule: it is semantic-blind. `Dark Lord Harry Potter`
    is a prefix-extension of `Dark Lord Harry` and NOT the inheritance-lordship
    concept a reader means by "lord of two houses".

- **`status=ongoing` filtered NOTHING, and the extractor had been emitting it
  for weeks.** The API coerced its `status` parameter through
  `StatusEnum.__members__` — the storage spellings `complete`, `in_progress`,
  `abandoned`, `unknown` — so every reader-facing synonym the search bar
  documents and parses (`ongoing`, `wip`, `incomplete`, `completed`) failed the
  membership test, the list came out empty, and the filter was DROPPED. Silent,
  and in the direction that looks like it worked: a request for unfinished
  works got every work, ordered plausibly. Measured on one AOT search:
  `ongoing` 21 results, `in_progress` 5.
  - Two vocabularies for one concept, and the one nobody tested was the one
    every caller used. `/api/search/extract` reads the word out of a post
    ("preferably ongoing") and hands it straight back, so the status half of
    every extracted request had never once been applied. Both now go through
    `query_parser.STATUS_WORDS`, and a test asserts the two modules share the
    same object rather than the same contents.
  - An unrecognised value still filters nothing rather than 422ing. A stray
    word in a shared link should not break the link.
  - **The probe had the same disagreement in miniature.** `_probe_count`
    approximated the filter as `status <> 'complete'`, which also admits
    `unknown` and `abandoned` — 7 works where the search returns 5. A probe
    that does not run the predicate the search will run is guessing, however
    close the guess looks.
- **The query STRING is the request; a field beside it is a field somebody has
  to remember to pass.** `/api/search/extract` returned `status`,
  `word_count_min` and `word_count_max` as fields, and `OutreachPanel` — the
  one caller written for it — read `query` and dropped all three. A post saying
  "ongoing, at least 150k words" was searched with neither constraint.
  - Both now go INTO the query string as the search bar's own shorthand (`wip`,
    `words:>150k`), which `parse_query` reads back and `/api/search` re-parses
    from `q`. One string carries the whole request to any client at all, the
    operator can see the filters in the box, and a pasted link keeps them.
  - **k/m suffixes are required and plain integers parse to nothing.**
    `words:>150000` yields `word_count_min = None` with no error — the same
    shape of bug as the status one, one layer down. `_k()` formats it.
  - `sort` is the exception and stays a parameter: quality is not expressible
    in the bar. The panel passes it on every run and the generated link carries
    it, or the link answers a different question from the screen.
- **A fandom abbreviation belongs to the fandom and to nothing else.** `asoiaf`
  and `tvd` are initialisms AND real freeform tags, so the n-gram lookup found
  each a second time and the query came out
  `fandom:"The Vampire Diaries (TV)" tag:"tvd"` — the tag narrowing to the few
  works carrying the abbreviation, inside the fandom it had already selected.
  Measured: **23 works against 2,742**. Same rule as the status words: a word
  consumed by one mechanism must not be spent again by another, checked once
  over the merged term list rather than in each path that builds it.
- **A bare first name is not the character the archives file.** `Damon` is a
  character on 76 works and `Damon Salvatore` on 9,134, so "damon centric tvd
  fics" was narrowed to the handful whose character list says only "Damon".
  `_resolve_pair` has canonicalised each half of a PAIRING this way since it
  was written; the rule was simply never applied to a character found loose in
  the prose, which is how most of them arrive. Measured: **23 → 1,409**.
  - Only ever UP. A "canonical" spelling with fewer works than the bare name is
    not the canonical spelling — it is a different person who happens to share
    a first name.
- **These four were found by running one request SHAPE across twelve fandoms**
  (TWD, MCU, ASOIAF, ATLA, MHA, PJO, AOT, HXH, TVD, Naruto, Star Wars,
  Supernatural), not by fixing the post that was reported. A rule derived from
  one post is a patch; a rule that holds across twelve unrelated vocabularies
  is an extraction rule. All twelve now resolve their fandom, and none returns
  zero.
  - Still open, and visible in that battery: a post whose only narrowing term
    fails the probe falls back to `fandom:"X" complete` sorted by popularity,
    which on Star Wars is a 41,513-work browse measured at **19.6s** — right at
    the statement timeout. A weak extraction is also a slow search.
- **A request names a FANDOM, a STATUS and a QUALITY, not only tags.** Ground
  truth from a real post: "any good TWD fics… preferably ongoing with SI main
  character". The extractor returned `or something` (471 works), `ongoing` as a
  TAG (450), `tbh` (332) and `twd` as a tag (163) — while The Walking Dead is a
  fandom on **20,498**, "ongoing" is a status filter and SI means Self-Insert.
  Every one of those IS a real tag somebody has used, which is exactly why the
  n-gram lookup finds them and why each had to be recognised first.
  - **Fandom abbreviations** come from `fandom_aliases`, mined from the naming
    convention, so this generalises rather than being a list.
  - **Status** → a filter. **Quality** ("good", "worth reading", "best") → a
    SORT, because no tag expresses quality and what the reader means is the
    works other readers actually read.
  - **`SI` and `OC` are TAG abbreviations**, not fandom initialisms, and need a
    small hand-written map — they abbreviate a concept rather than a title, so
    no rule derives them. Two letters also match nothing in an n-gram lookup.
  - Recognised terms are then SUPPRESSED from the term list: returning
    `ongoing` as a subject spends a slot saying what the status filter already
    says, and says it worse.
  - Result: `fandom:"The Walking Dead (TV)" tag:"Self-Insert" wip` sorted by
    popularity → 32 works, led by SI fics.
- **An alias that fires on an ordinary word is worse than no alias.** `any`
  resolved to `Akatsuki no Yona | Yona of the Dawn` and hijacked a post that
  said "any good TWD fics"; `si` resolved to `SK8 the Infinity` on a post that
  meant Self-Insert. Both are now in the miner's STOPLIST.
  - **`got` is in it too, and that costs Game of Thrones its initialism** — a
    deliberate trade, recorded in a test. The asymmetry decides it: a wrong
    fandom filter silently returns a different fandom's stories and the reader
    cannot see why, while an absent alias costs four keystrokes. GoT is still
    reachable by name and `asoiaf` still resolves.

- **A bulleted fic-finder post is a LIST of constraints, one per line, and
  reading it as one bag of words threw that structure away.** "harry is lord of
  at least 2 houses" became the word "houses", which matched `House`, the
  television programme. Per line through `read_request` + `resolve_trope_tags`
  it resolves to `Harry is Lord Potter`. The extractor now does both: lines
  first, loose n-grams after, and `from_line` marks the difference so a later
  sort cannot flatten it — a flat sort by count put `Plot` (5,367, a stray noun
  in "decent plot/characters") above `Albus Dumbledore Bashing` (3,481), which
  the reader had asked for in as many words.
  - **The "I have read" list is split off first.** Read as wants it is actively
    misleading: a post listing "sarcasm and slytherin" as a fic already
    finished returned `Sarcasm` and `Slytherin` as things it was asking for.
    Handed back as `already_read` for /api/search/taste.
  - **Punctuation and caveats block resolution.** "bashing
    (dumbles/weasleys/hermione)- but not WAAAYYY TOOOO much" resolved to
    NOTHING; the same words spaced out and cut at "but" give
    `Albus Dumbledore Bashing`. Readers group alternatives with brackets and
    slashes and qualify their own asks, and both defeat a window match.
  - **The BIGGEST spelling, not the first.** `resolve_trope_tags` returns every
    way the archives write a concept and the search path ORs them; a single
    `tag:"…"` cannot, so `tags[0]` silently picked one variant — the bashing set
    spans six spellings and 3,481 works, and the first is not the largest.
  - **Three right answers make a wrong query.** `Harry is Lord Potter` (82),
    `Magically Powerful Harry` (48) and `Politically Powerful Character` (24)
    are each exactly what the post asked for, and together they return **zero**.
    The query is now built one term at a time and PROBED — capped count, GIN
    containment, word-count constraint included — keeping a term only if at
    least 3 works survive it. If nothing survives, it falls back to the single
    best term: the probe is a refinement, not a gate, and an empty query is the
    failure this endpoint exists to prevent.
  - Measured end to end: the Harry/Daphne post → 64 works; the lordship post →
    `tag:"Albus Dumbledore Bashing" words:>150k` → 160 works, all 150k+.
- **Three faults a dry run of the verification script found, none of which any
  test was asking about.**
  - **`tests/check-robots.py` had been failing since the commit that rewrote
    the crawler groups.** It still asserted `/story/` was ALLOWED for Applebot,
    which was true until Applebot was deliberately held to the hubs and false
    from that commit onward. A check that is permanently red is a check nobody
    reads. It now asserts BOTH halves of that decision — story tail refused,
    hubs and sitemap allowed — because getting only the first half is how the
    policy quietly comes undone.
  - **Two spellings of one concept became two requirements.** "self-insert"
    produced `tag:"Self-Insert" tag:"Self Insert"` — different facet values,
    same concept, AND-ed, so the query demanded a work carrying both. Neither
    `_implies` nor the value dedup can see it: one compares characters with
    word boundaries and a hyphen is not a space, the other compares values
    exactly. Terms are now deduped on a punctuation-stripped key, and the loser
    joins the winner's spellings so the probe still ORs them.
  - **A tag that NEGATES a concept was being treated as a spelling of it.**
    Asking for self-inserts put `Not a self insert` in the resolver group, and
    the group is what the probe ORs — so a concept could survive on the
    strength of works saying the opposite of what was asked.
- **Comparing totals is the wrong way to test the two content tiers, and the
  first version of the check failed on a healthy system.** `explicit=true`
  legitimately admits far more works than `include_underage` does (1,466
  against 1,075 on the Harry/Daphne search), so `explicit <= underage` is not
  the invariant. The property that matters is INDEPENDENCE: turning underage on
  must still add works when explicit is already on — 1,466 to 1,647 — because
  if explicit had been quietly unlocking tier 1 there would be nothing left for
  the underage toggle to reveal. Confirmed at the row level too: zero
  underage-gated works pass the predicate an `explicit=true` search applies.

- **A crossover is more than one FRANCHISE, not more than one fandom tag — and
  the first six results on a real post were all crossovers.** Reported from the
  live site on the TWD fic-finder post: Avengers, Supernatural, Lord of the
  Rings, Teen Wolf, Resident Evil, and a **twenty-one-fandom** SI collection in
  which The Walking Dead is one entry. Arithmetic, not bad luck — a crossover
  carries several fandoms, so it matches several fandom searches and draws
  readers from all of them, and on a popularity sort it outranks a work written
  for the fandom that was asked for. 74 works, 15 genuine crossovers, and they
  took the whole of page one.
  - **The flag had to be fixed before it could be trusted to hide anything.**
    `is_crossover` was `len(fandoms) > 1` written out in FIVE places, and AO3
    fandom tags are not franchises — an author files one story under every
    spelling that fits. `crossover.py` strips the archive's naming furniture
    (the `(TV)` disambiguator, the `- All Media Types` / `- J. K. Rowling`
    tail, the subtitle after `:`, the `& Related Fandoms` umbrella, a leading
    article, and the original-language half of `原神 | Genshin Impact`) and
    counts distinct franchises. Not a synonym table: the furniture is a
    convention the archive follows, so this generalises to fandoms nobody has
    heard of.
  - **The first measurement of the error rate was wrong and drove a wrong
    recommendation.** A crude "do all the fandoms share a first word" proxy
    said 33%, because a leading article collides everything — `The Walking
    Dead` and `The Avengers` both reduce to "the". With a real franchise key it
    is **20.6%**. On the TWD post: 22 flagged, 15 genuinely crossovers, 7
    wrongly. Check what a proxy actually measures before quoting it.
  - **The rule exists twice, in Python and in SQL, and a test asserts they
    agree** over the 500 largest fandoms in the index — the same arrangement as
    the two query parsers, and for the same reason. The repair rewrites
    millions of rows and pulling each into Python would take days.
  - **The known collision is recorded, not fixed.** `Avatar: The Last
    Airbender` and `Avatar (Cameron Movies)` both reduce to `avatar`, so a
    crossover between them reads as one franchise. That direction is safe — a
    crossover kept is a work the reader can see and judge, a work wrongly
    hidden is invisible — and it is rarer than the false positives the subtitle
    rule removes. A test says so if it ever stops being true.
  - `crossover.run` joins `_curation_loop` beside the gate repair, for exactly
    that reason: the flag is written by five importers and the crawler, so it
    is right for anything written since the definition changed and wrong for
    everything before it, and no trigger retrofits a definition onto old rows.
  - **The extractor now excludes crossovers when the post NAMES a fandom** and
    never mentions one. Only the extractor — "TWD fics" typed into the search
    box is not the same statement as a fic-finder post naming one fandom and
    asking for stories in it. Measured on the real post: page one went from six
    crossovers to none.

- **"no crossovers please" was searching FOR crossovers, and a blanket default
  would have been worse than the bug.** The extractor returned `tag:"Crossover"` —
  the one thing the reader had ruled out — which is "no harems" all over again:
  the word is in the post either way, so only the words AROUND it separate a
  want from a refusal. The refusal patterns are therefore tested FIRST, because
  "no crossovers" contains "crossovers".
  - **Deliberately NOT a default, and the measurement is the reason.** Hiding
    crossovers unless asked is a reasonable instinct and `is_crossover` cannot
    carry it: the column is `len(fandoms) > 1`, and AO3 authors routinely tag
    several spellings of ONE franchise — `Star Wars - All Media Types` beside
    `Star Wars: The Clone Wars (2008)`, `Percy Jackson … - Rick Riordan` beside
    `Percy Jackson … & Related Fandoms`. Over a 20,000-work sample of flagged
    AO3 works, **33% have fandoms that all share a first word**, i.e. are not
    crossovers at all. On the real TWD post: 74 works, 22 flagged, **11 of them
    wrongly**. Excluding by default would delete a third of the answer, half of
    it for a bug, and do it invisibly — fewer results look exactly like a
    search that worked.
  - It would also skew the archive mix, which is the thing this site exists to
    get right: AO3 is **16.19%** flagged and FF.net **5.33%**, and that gap is
    coverage rather than fact — the FF.net bulk dumps carry one fandom per row,
    so `false` there means "unknown". Same trap as `status` before the SQLite
    importer filled it in. A default would be near-no-op on FF.net and
    aggressive on AO3.
  - That "fix the column first" note was acted on the same day — see the entry
    above.
- **Three more rules from the same twelve-fandom battery, each a word spent
  twice or weighed wrong.**
  - **"longfics and one-shots welcome" is a reader saying they do not care.**
    It read as `word_count_min = 50,000` from one phrase and
    `word_count_max = 10,000` from another, and together that is
    `words:50k-10k` — a range no work can satisfy, from a post that said in as
    many words that any length was fine. A contradiction is not a narrow
    request, so BOTH ends are dropped. It was invisible until the word count
    went into the query string: a filter nothing applies cannot be seen to be
    wrong. Measured on the Harry/Daphne post: **76 works → 181**.
  - **The words that asked for a length are spent.** `Words` is a real tag on
    254 works and `at least` on 186, both straight out of "at least 150k
    words", both saying nothing about any story, and both competing for one of
    three slots. Same rule as the status words and the fandom abbreviation,
    checked at the same single point.
  - **A bare first name ranks behind what the post spelled out.** "harry is
    lord of at least 2 houses" names a concept AND, from the single word
    "harry", a character on 152,287 works. On frequency the character wins, so
    the query spent two of three slots on `char:"Harry Potter"` and
    `char:"Hermione Granger"` — the name the reader wanted BASHED — and had
    none left for lordship or politics. Measured: 77 works against 127. Two
    parts to the fix, and both were needed: a canonicalised name is ranked on
    the BARE name's count (the swap fixes a spelling, it is not new evidence),
    and a bare name sorts behind every concept resolved from the same line.
    Same asymmetry as the rejected "characters outrank tags" rule, one level
    down.
- **The series length ADD-ON: three 60k works the author filed as one story are
  a 180k read.** Asked for directly — "for word count we could maybe include
  series if all works in series add up to 150k+ … could be an 'add on'".
  Measured at a 150k floor: 18,077 series of more than one work qualify,
  holding 158,059 works, and **147,929 of those are individually shorter** and
  could not otherwise be found by anybody asking for a long read.
  - **It WIDENS and never narrows**, and that is the difference between an
    add-on and a filter: the clause is OR-ed onto `word_count >= n`, so a long
    standalone still comes back. It is also deliberately NOT `series:true` —
    filtering to works that are in a series would narrow a request for a long
    read.
  - **`member_count > 1` is the guard**, and it is the reason this was checked
    before it was built: 75,622 one-work series are real (authors file a
    standalone in a series, or intend to add more), and for those the total is
    just that one work, so counting it would mean applying the same filter
    twice and calling it a feature. It is also the predicate of
    `ix_series_total_words`, so the guard is free.
  - **A COLUMN, not a join, and the measurement decided it.** As the semi-join
    it reads as — `id IN (SELECT story_id FROM series_works JOIN series …)`
    OR-ed onto the length filter — neither side can use an index: **16.2s
    against 0.9s** on `tag:"Time Travel" words:>150k`, with a second query
    timing out at 30s. As `stories.series_total_words` beside `word_count` it
    is a BitmapOr of two index scans and measured FASTER than the unmodified
    filter. `series_wordcount.py` maintains it; `_curation_loop` runs it
    weekly, beside the content-gate repair and for the same reason — both are
    a denormalised column that has to be rebuilt after a bulk change to what
    it was derived from.
  - Staleness is in the safe direction: the add-on widens, so a total that has
    not caught up fails to admit a work rather than admitting one that does not
    qualify.
  - **`series:count` is bar syntax**, mirrored in both parsers, so it reaches
    the reader as a chip, survives a paste, and travels in a link — and the
    extractor appends it whenever a post named a length. There is a sidebar
    checkbox too, shown only when a minimum is set, because a control that
    changes nothing teaches readers the filters are decorative.
  - **The fill walks the index once.** The first draft drew each batch from
    "rows that are currently wrong", which terminates and resumes — and the
    planner satisfies the LIMIT by walking `ix_series_works_story` from the
    beginning every time, discarding what it has already fixed, so batch 21
    probes a million rows to find the last fifty thousand. A keyset cursor over
    `story_id` is O(n). Even so it is ~10 minutes per 50,000-row batch on this
    disk: 1.06M member works, so budget hours and run it detached.
  - **Adding the column to a live 20.5M-row table needed the worker stopped.**
    `ALTER TABLE … ADD COLUMN` is metadata-only in PG 11+ and still needs
    ACCESS EXCLUSIVE, which conflicts with every reader. Three minutes of
    retries at a 1s `lock_timeout` never won it while the crawler and the gate
    backfill were running; with `docker compose stop worker` it landed on the
    seventh attempt. Use a `lock_timeout` and retry — NEVER let the DDL queue,
    because a waiting ACCESS EXCLUSIVE blocks every reader behind it, and
    queueing behind a nine-minute UPDATE is a nine-minute outage. That happened
    once during this work; the symptom is every search 503ing while
    `pg_stat_activity` shows a `CREATE`/`ALTER` in `wait_event_type = Lock`.
    Build the index `CONCURRENTLY` by hand first, as the init_db note says.
- **The series maker is not broken, and the 75,849 one-work series are real.**
  Checked before building anything on it. 99.8% are `source='explicit'`, 100% of
  those carry an AO3 series id, and **AO3's own `work_count` says 75,622 of them
  contain exactly one work**. Authors genuinely create one-work series — to
  group a standalone, or intending to add more. Nothing to fix; a series
  word-count feature would simply need to ignore singletons, where the total is
  just that one work anyway.

- **A pairing is the SUBJECT of a request and has to beat a million-work tag.**
  On the Harry/Daphne post, looking each half up as a loose character was not
  enough: bare "Harry" is on 541 works and bare "Daphne" on 79, so both sank
  below every generic tag and the one thing the reader asked for never
  appeared. `_resolve_pair` expands each half to the canonical character
  (`Daphne` → `Daphne Greengrass`, most-written wins among four) and looks up
  the relationship holding both — `Daphne Greengrass/Harry Potter`, 1,035
  works. It then leads the list.
  - That is the ONE exception to ranking by frequency, and it needs a RESOLVED
    pairing. The rejected "characters outrank tags" rule let `God`, from "for
    the love of God", beat every tag in the post.
  - **The fandom is inferred from the works, not a lookup table.** Sample 300
    works carrying the pairing and take the fandom most of them list: 18ms, and
    233 of 300 say `Harry Potter - J. K. Rowling`. A two-thirds majority is
    required — a bare plurality means the pairing crosses fandoms and naming
    one would narrow to the wrong half. No per-fandom code, so it works for
    pairings nobody has heard of.
  - **A variant spelling is swapped for the one archives file under.** A reader
    writing "fluffy" means `Fluff` (1,130,841 works); the literal match is
    `Fluffy` (8,964). Same word, two orders of magnitude apart. Generalises
    without a synonym list: a tag that is a prefix of another and vastly better
    attested is its canonical spelling.
  - The query spends its three slots on the pairing and two QUALITIES, not the
    fandom — the pairing already implies it, so that slot would narrow nothing
    while dropping something the reader asked for. Result on the real post:
    `ship:"Daphne Greengrass/Harry Potter" tag:"Fluff" tag:"Romance"` → 76
    works, led by *The House of Potter-Greengrass*.

- **Condensing prose is the wrong shape; EXTRACT instead.** Stripping framing
  from a 200-word post leaves a 180-word query, and every term in a search is a
  requirement. `/api/search/extract` matches every 1–4 word run in the post
  against `facets` in one indexed query — **6ms for a whole post** — so the
  index says which words are searchable rather than the frontend guessing which
  were framing.
  - **Rank by how much the ARCHIVE uses a term.** Two orderings were tried and
    both were wrong in instructive ways. By LENGTH: `one shots` (1,561 works)
    and `i just` (113) outranked `Fluff` (1,130,841), because two words beat
    one. By KIND: `God` — from "for the love of God" — outranked every tag in
    the post, because a 1,113-work character beat a million-work subject just
    for being a character.
  - Runs made only of function words are rejected; contractions are cut at the
    apostrophe, so "don't" tests as "don". Without that, `I don't` (a real tag,
    60 works) passed as a subject.
  - **"Harry/Daphne" is how readers write a pairing.** Splitting on punctuation
    lost the only mention of the second character in that entire post — it
    never writes either full name. Each half of an `A/B` or `A x B` run is now
    looked up as a character.
  - Offered, not applied: eight candidates come back as clickable chips because
    extraction from prose is genuinely ambiguous, and a person is better placed
    than the ranker to know that "God" was a figure of speech.
  - **Use a dict, not a positional tuple, when the sort key changes.**
    Reordering it silently broke the unpacking twice — once reporting every
    count as 1, once as -3.

- **Everything derived from the fic-finder corpus lives in the SEARCH, not the
  outreach panel.** Negations, written word counts, multi-trope resolution and
  fandom abbreviations are all in `query_intent.py`, which `api/search.py` calls
  for every query on the public endpoint; `taste` and `exclude_ids` are public
  API routes. The panel is a client of the same endpoints and adds only paste,
  condense and a reply template — it can demonstrate nothing a visitor cannot
  do by typing.

- **The "I have already read" list is the richest signal in a fic-finder post,
  and it was being thrown away.** `/api/search/taste` resolves the titles,
  reduces them to the tags those works have IN COMMON, and hands back a runnable
  query. Measured on four slow-burn Drarry fics: derives
  `ship:"Draco Malfoy/Harry Potter" tag:"Slow Burn" tag:"Romance"` → 292 works.
  - **Shared, not union.** One work's tag list describes that work; what several
    have in common is taste. `TASTE_MIN_SHARED = 2`.
  - **Provenance tags had to be excluded or they win outright.** `ffnet_dump`,
    `ao3_meta_dump` and `hf_meta_2024` live in the same array as real tags —
    four famous works shared those and the recs markers and NOTHING else, so
    the derived "taste" was which script imported them. `content_tags()` in
    provenance.py already existed for this distinction; the recs markers are
    the same kind of thing and that module does not know about them.
  - **Rarer shared tags rank first.** A tag everybody uses says nothing about
    this reader — `Fluff` is on 1.13M works, so sharing it is arithmetic, not
    preference.
  - Title resolution is EXACT and most-read-wins, with every match returned for
    checking. The index holds eight works called "Monochrome", and a reader
    naming a title without an author almost always means the one everybody has
    read. Unmatched titles are reported, not swallowed — "Sarcasm and
    Slytherin" and "Daft Morons" are genuinely not indexed, and a taste quietly
    built from half a list would be worse than none.
  - Prefix matching was tried and rejected on cost: `LIKE 'daft moron%'` over
    `lower(title)` took **18 seconds**.
  - `exclude_ids` completes it. Recommending back what somebody has told you
    they have read is the one answer they have ruled out — verified that
    *Running on Air* drops out of its own derived search.

- **"No harems" was searching FOR harems.** From a corpus of real fic-finder
  posts: one request listed NINE negative conditions against six positive ones,
  and none of it was parsed, so the words went into the positive query.
  Measured before the fix:

      harry potter no harem  ->  "The Harem War" first, and a work tagged
                                 `Harry Potter Has a Harem` third
      no character death     ->  works tagged `Character Death`

  Worse than zero results: the reader is handed the opposite of what they asked
  for, and it looks like it worked.
  - `_extract_negations` resolves the subject against the SAME tag vocabulary
    the positive path uses, which is what makes it general instead of a list of
    tropes somebody thought of. The window logic also settles where the
    negation stops: "no character death fluff" excludes `Character Death` and
    KEEPS "fluff", which no punctuation-guessing would have decided.
  - **`no`/`not` are gated on the query reading as a request; `without` and
    `excluding` are not.** Resolution alone is NOT a sufficient guard, and this
    was measured rather than assumed — with the gate off, `no country for old
    men` excluded `Grumpy Old Men`, `no way home peter parker` excluded
    `Homeless Peter Parker`, and `the boy who had no name` excluded `Names`.
    The vocabulary is 1.57M freeform strings and the match is fuzzy by design,
    so almost any phrase resolves to something.
  - The cost is stated in the code: `harry potter no harem` typed bare is not
    recognised as a request, so its negation is missed. It fails by doing
    nothing rather than by excluding the wrong thing — wrong exclusions are
    invisible, missed ones are not.
  - Single-word subjects ("no harems") need an EXACT vocabulary match, not a
    substring one. `LIKE '%home%'` is how `no way home` matched
    `Homeless Peter Parker`.
- **`_strip_frames` was eating the "with" of "without".** The framing pattern
  listed `with` as a connective after the fic-noun with no trailing `\b`, so
  "looking for a fic without character death" became "a out character death" —
  the negation destroyed before anything could read it, and "out" searched for
  as a word. Pre-existing, found only because negation parsing tripped over it.

- **"at least 150k words" set the floor to 50,000.** The vague `long` qualifier
  matched "very long" elsewhere in the post and the number the reader actually
  gave was ignored. Being handed 50k fics when you asked for 150k is worse than
  no filter, because it looks like it worked. `_explicit_word_counts` now runs
  BEFORE the vague qualifiers and handles "at least 150k", "over 100k", "more
  than 200,000", "150k+", "under 50k", "between 100k and 300k".
  - **The trap, from the same post: "harry is lord of at least 2 houses".**
    Identical comparator. A bare number is only a word count when it carries a
    k/m suffix or reaches 1,000 — nobody asks for a fic over two words long.
    Without that condition this reads a man's house count as a manuscript
    length. "at least 3 horcruxes" and "more than 2 years later" are asserted
    too.
  - Verified across ten fandoms — HP, Naruto, MHA, Marvel, Star Wars, Percy
    Jackson, Supernatural, Teen Wolf, Dragon Age, BTS — nine of ten spanning
    two or more archives. Percy Jackson comes back FF.net-majority (303 v 40),
    which is the case no AO3-only search can make.
- **`\b` in a non-raw Python string is a BACKSPACE.** Nine word-boundary
  anchors were written into `query_intent.py` by a generator script whose
  replacement string was `'''...'''` rather than `r'''...'''`, so every `\b`
  became `\x08`. The regexes compiled, matched nothing, and reported no error —
  the feature simply did nothing. If a new pattern silently never fires, check
  for control characters before rewriting the logic.
- **The outreach panel is a fifth admin tab, not a document.** The traffic tab
  says nobody is coming; `OutreachPanel.tsx` is the one that does something
  about it. Paste a fic-finder post, condense it (framing, bullets and the whole
  "I have read" list are stripped), search, see the archive split, copy a reply.
  It reads the PUBLIC search API so what it shows is what a reader following the
  link will see, and it posts nothing anywhere.
  - It flags whether a search is worth linking: `spans archives` (good),
    `one archive only` (weak), `too broad to show the split` (narrow it),
    `nothing found` (fewer words). The whole pitch is that three archives were
    searched, so a single-archive result is a true answer and a poor
    advertisement.
  - A condensed 30-word post still returns 0 and that is inherent — every term
    in an AND query is a requirement. The panel's job is to get you to an
    editable starting point; the guidance says two or three tropes beats forty
    words, and the measured difference is 0 against 77 works.

- **A fic-finder post names several tropes and only one of them was resolved.**
  `resolve_trope_tags` finds the single longest window that IS a tag and hands
  the rest back as words — right for "time travel naruto", where the leftover
  bounds the trope, and wrong for the query shape this site exists to serve.
  Reported from a real r/FanFiction post; the reduced case:

      powerful harry dumbledore bashing     →  0 results

  It resolved `Powerful Harry Potter` (653 works) and threw "dumbledore
  bashing" at the text index, where `websearch_to_tsquery` ANDs every word
  against title+summary+author+tags as one flat document. So it asked for works
  tagged `Powerful Harry Potter` that ALSO literally contain "dumbledore" and
  "bashing" — while 3,481 works carry `Albus Dumbledore Bashing` and the reader
  would have taken any of them.
  - `resolve_intent` now keeps resolving the leftover into `extra_tag_groups`,
    OR within a group and AND between them, because a fic-finder post is a list
    of conditions and they are conjunctive. Bounded at three: four separate
    tropes is rare and each pass is another vocabulary lookup.
  - **Measured: 0 → 1,031 works**, top hit *Harry Potter and the Prince of
    Slytherin* — which the original poster had listed as one they had read and
    enjoyed. `dumbledore bashing time travel` 0 → 750,
    `weasley bashing independent harry` → 118 across all three archives.
  - The bounding case is asserted separately so the chain cannot eat it:
    "time travel naruto" must keep "naruto" as a word, or `Time Travel` returns
    the 44,000 works that are not Naruto.
  - **OR was measured and rejected as the fix.** Relaxing AND to OR on the same
    query matched 20,000+ rows in 4.4s against 62ms — it matches anything
    containing "harry" or "power", which is slower AND worse.
  - Still open: explicit word counts in English. "at least 150k words" is not
    parsed — `read_request` gives `word_count_min=50000` from the word "long"
    and ignores the number.

- **Zero-result searches now suggest a spelling.** A real visitor searched
  `hsrry potter wandcrafter` and got nothing, with `Harry Potter` — 686,558
  works — one transposed letter away. `_did_you_mean` trigram-matches the WHOLE
  query against `facets` (word-by-word returns noise: "wandcrafter" alone best-
  matched the tag `After wano`).
  - **Ranked by `similarity * ln(count)` with a floor of 200, and the floor is
    the feature.** Similarity alone suggests the reader's own mistake back at
    them: `hermoine granger` matches the misspelled facet `Hermoine Granger` at
    1.000 and 55 works really do spell it that way. With the floor it returns
    `Hermione Granger` (102,007). Same rescue for `steve rogets`, which matched
    a 15-work `Steve Roger` before and `Steve Rogers` (144,593) after.
  - Suggests, never rewrites: the correction is a FACET, so searching it for the
    reader would drop the word they cared most about.
  - **Write `%`, not `%%`.** SQLAlchemy escapes it when compiling `:q` to
    psycopg2's paramstyle, so `%%` reaches Postgres literally and raises
    `operator does not exist: text %% unknown` — which the except swallows,
    leaving a feature that silently returns nothing and reports no error.
- **Two accounts existed, and the reasons to make one were stated only behind
  the decision.** `WhyAccount.tsx` is the case, on the login page: following
  WIPs across three archives (the one thing no archive can do), a shelf that
  survives a cleared browser, progress that moves between devices. Every line is
  a thing the code does — if a feature goes, the line goes, because this is the
  screen where a reader decides whether the site is honest. The library's
  signed-out note now COUNTS what is at stake ("your 12 bookmarks and 3 stories
  in progress live only in this browser") and appears only when there is
  something to lose; telling someone with an empty shelf that their nothing is
  at risk is nagging.

- **The offline story shell was the one precache entry that could never
  succeed.** `gen-sw-precache.js` asks for `/story/offline-shell` and
  `/story/offline-shell/chapter/1`. The chapter route never blocks on a lookup
  so it answered 200 and cached; the story route asked the API, got a confirmed
  404 for an id that is not a work, and called `notFound()`. Measured over 24h
  of origin logs: **176 requests, every one a 404.**
  - Not fatal, which is why it survived: `offline-shell` is not in the worker's
    ESSENTIAL list, so install counted the miss and carried on. The cost was
    quieter than a broken install — offline CHAPTER reading worked and offline
    STORY pages did not, which is the half a reader hits first.
  - `OFFLINE_SHELL_ID` is now answered inside `lookupStory` without a round
    trip, and carries `noindex` with no canonical of its own. A genuinely
    missing uuid still 404s — verified, because the fix is one `if` away from
    turning every dead story link into a soft 404.
- **A managed challenge cannot be verified with headless automation, and trying
  to is how you talk yourself into reverting a working rule.** After the
  `/story/` botnet challenge went live, Playwright sat on "Just a moment…" for
  8s on both a direct arrival and an in-site navigation. That looked like every
  story page being dark for every reader. It was not: managed challenges are
  *designed* not to pass headless Chromium, so that test cannot distinguish
  "botnet blocked" from "everyone blocked". The origin log could, and did —
  within the hour, **53 × 200 to story pages from Chrome 139/152 and Firefox
  130, across 44 distinct IPs with several making 2-7 requests each**. That is
  session-shaped human traffic; the botnet's signature was one request per IP
  across 14 rotating Chrome 99-136 strings, and it is gone. Story-page load at
  the origin fell ~91%. Check the origin, not the automation.

- **Relevance was ranking the ARCHIVE, not the story.** The formula's popularity
  term was `ln(1 + kudos + hits/20)/13.8` — raw, cross-site — while the browse
  path had used the site-normalised `popularity` column since it existed. The
  three archives do not count the same things on the same scale, which is the
  whole argument `popularity_rank.py` opens with, and the effect measured over
  the index is:

  | site | p50 | p90 | p99 | max |
  |---|---:|---:|---:|---:|
  | fictionalley | 0.2692 | 0.3930 | 0.5473 | 0.8117 |
  | ao3 | 0.0000 | 0.2883 | 0.5080 | 1.0000 |
  | ffnet | 0.0000 | **0.0000** | 0.3898 | 0.7958 |

  Nine in ten FF.net works scored exactly zero on a term multiplied by up to
  3.5. `harry potter` returned 20 AO3 works and nothing else on page one while
  the same query at 100 results was 69% FF.net.
  - Now `coalesce(popularity, <old expression>)`. Not a swap: 114,768 AO3 rows
    have engagement but no percentile yet (popularity_rank runs offline and the
    crawler has moved on), and scoring those 0 would demote the freshest works.
    The 18M rows with no engagement at all score 0 either way.
  - Measured after: `harry potter` ao3=6/ffnet=14, `hogwarts` 11/9, `naruto`
    15/5. Every canary in the file still passes — `all the young dudes` returns
    the 322,055-kudos work, `the arithmancer` the 4,461-kudos one, and
    `harry potter and the methods of rationality` now returns the FF.net
    original first, which is the right answer and was not what it did before.
  - `coffee shop au` and `enemies to lovers` stay AO3-only and that is correct:
    in category mode tags carry weight 1.0 and those are AO3 tag-vocabulary
    concepts. Which leads to the real remaining gap —
- **The cross-archive gap is DATA, not ranking, and it is worth not
  misdiagnosing again.** Coverage by site, measured 2026-09-09:

  | site | rows | tags | summary | relationships | characters |
  |---|---:|---:|---:|---:|---:|
  | ao3 | 13.96M | 99.9% | **15.9%** | 62% | 68% |
  | ffnet | 6.57M | 100% | 100% | **1.3%** | **1.7%** |
  | fictionalley | 30k | 100% | 99.7% | 18% | 81% |

  - `fic_doc` already includes `summary` for every site, so summaries ARE
    searched — they simply sit in band D (0.1) where tags sit at 1.0.
  - A ship query can barely reach FF.net because FF.net files pairings as
    CHARACTERS and only 1.7% of its rows have any. Bridging ship → "both
    characters present" was measured and rejected: on `drarry` it adds 442
    FF.net works unguarded but 25,071 AO3 works that merely contain both
    characters, and guarding it to rows with no relationship data at all adds
    just 130. The lever is `ffnet_enrich.py`'s Wayback backfill, one request
    per story, not a query change.
- **Exclude filters multiplied on every reload.** `buildParams` combined the
  sidebar chips and the parsed search bar with a raw spread for the four
  exclude fields (`[...excTags, ...pq.excTags]`) while every include field used
  `merge()`. Both sides hold the same value: the URL seeds the chip, the chip
  is serialised into the bar, the bar is parsed back — so `exclude_tags` grew by
  one copy per load, for ever. Now merge() for all of them, with a reload loop
  in `queryParser.test.ts` that fails if it ever regrows.

- **Nearly every request this origin serves is a robot, and the bet on Apple and
  Amazon did not pay.** Measured 2026-09-08 over 9.7h and 102,195 requests:

  | agent | reqs | % origin | into /story/* | per day |
  |---|---:|---:|---:|---:|
  | Applebot | 25,394 | 24.8% | 98.6% | ~62,759 |
  | rotating-UA botnet | 20,221 | 19.8% | 100% | ~50,000 |
  | Amzn-SearchBot | 3,496 | 3.4% | 95.8% | ~8,640 |
  | YandexBot | 473 | 0.5% | 1.9% | ~1,169 |
  | bingbot | 6 | — | 0% | ~15 |
  | **Googlebot** | **1** | — | 0% | **~2** |

  48,673 of those requests were `/story/*` and 48,612 of them were one of the
  three crawlers above. There is essentially no human traffic to story pages.
  - **Applebot and Amzn-SearchBot now get the hubs and not the tail.** Full
    groups in robots.txt, plus a path-scoped edge rule for Applebot, which has
    ignored `Crawl-delay: 10` throughout (1.38s between requests, against
    Amzn-SearchBot's 10.00s to the tenth of a second). This is not a block: the
    home page, both index pages and all 11,196 hubs stay open. What is refused
    is a 20.5M-page space that at ~62,000 unique URLs a day takes 300+ years to
    finish, cannot be edge-cached because every URL is fetched once, and has
    never returned a reader from either archive.
  - **The robots.txt trap this walked into and out of:** a crawler obeys the
    most specific matching group and ONLY that one. The moment
    `User-agent: Applebot` exists, Applebot stops reading the `*` group — so a
    group containing just `Disallow: /story/` would have re-opened `/admin`,
    `/api/`, `/account` and the whole `/*?` search space to it. Every line of
    the `*` group is repeated in both new groups deliberately. Edit one, edit
    all three.
  - **The residential-proxy botnet gets a managed challenge, not a block.**
    20,221 story requests from **19,705 unique IPs** — one request per address —
    cycling 14 browser UA strings in near-perfect round robin (1,353–1,465 each)
    over 20,032 distinct URLs. Address and agent are both what a network of that
    shape exists to defeat. What it cannot fake cheaply is a browser: 20,221
    story pages against 138 static assets, 0.7%, so it reads the server-rendered
    HTML and never executes the page. A managed challenge is that exact test.
    - Scope is narrow because the measurement allows it: this botnet made ZERO
      requests outside `/story/`. Carve-outs are `not cf.client.bot` (so
      Googlebot and bingbot are exempt — getting that wrong would take 2
      requests a day to zero), the `sat=` cookie, and `/story/offline-shell`,
      which is the service worker's precache target and cannot answer a
      challenge.
    - **`_rsc` is deliberately NOT exempted.** Next's client-side navigation
      fetches `/story/<id>?_rsc=…` and a fetch() cannot solve a challenge, so
      exempting it looks like the kind thing to do. It is a bypass costing one
      query parameter, and the botnet already sends it (223 requests). The
      degradation is acceptable instead: the RSC fetch gets challenge HTML, Next
      falls back to a full navigation, the reader answers once, and Cloudflare
      honours cf_clearance on everything after.
    - The cost is real and worth restating whenever this is revisited: an
      anonymous reader arriving from a search engine answers a challenge first.
  - **`urllib.robotparser` cannot verify any of this.** It implements neither
    `*` nor `$`, so it reports `/?q=` as ALLOWED for every agent including the
    long-standing `*` group. It is still worth running for group structure and
    blank-line discipline; it is not evidence about a wildcard rule.

- **A sitemap index, because honest per-URL timestamps do not fix a file-level
  lie.** Fixing `content_at` (below) stops individual entries claiming a change
  that did not happen. It does not change the fact that ONE hub moving means the
  single file this site publishes has moved, and a crawler learning that had to
  re-read all 11,196 URLs to find out which. `/sitemap.xml` is now a
  `<sitemapindex>` over seven children — `core` (6 static pages + the top 500
  ships + the top 500 fandoms) and `ships-1..3` / `fandoms-1..3` at 2,000 each.
  Each child carries its own `<lastmod>`, so churn is contained in one 2,000-URL
  file instead of touching everything.
  - **No URL appears in two files** (verified: 11,196 emitted, 11,196 unique).
    Overlap is legal and crawlers dedupe, but it would make Search Console's
    per-file coverage double-count the top 500 — and being able to read those
    numbers per tier is most of the reason to segment at all. Against one file
    of 11,196 the coverage report was a single number that said nothing.
  - **The bulk chunks are ordered by SLUG, not by work_count.** This is the
    subtle one and `lib/sitemapData.test.ts` locks it. work_count moves for most
    hubs daily, so ordering the chunks by it means a hub near a 2,000 boundary
    changes rank, crosses it, and changes the contents of TWO files on a day
    when no page changed — segmenting to isolate churn and then ordering the
    segments by the churning value gets the daily-rewrite problem straight back,
    one level up. Core stays in work_count order; that set moves slowly.
  - Two places had to learn about the children, and both were silent failures:
    the catch-all `headers()` rule in `next.config.ts` excludes `sitemap.xml` by
    name, so `/sitemaps/*` would have been served `no-cache` despite having a
    rule of its own; and `deploy/cloudflare_cache_rule.py` reconciled by
    DESCRIPTION only, so widening a rule's expression printed "present" and sent
    nothing. It now compares expressions and PATCHes — the file's own comment
    says it exists to stop a rule and its origin header drifting apart, so that
    could not be the one kind of drift it ignored.
  - What this does NOT do: make Google crawl more. Googlebot is not blocked —
    its one request in the 9.7h window was a verified 66.249.74.39 taking a 304
    on robots.txt. Two URLs a day is crawl DEMAND on a young domain, which no
    file on this server sets. The index makes sure the budget that does arrive
    is not spent re-reading unchanged URLs.

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
