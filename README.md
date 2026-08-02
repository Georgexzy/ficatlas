# FicAtlas

A unified search engine for fanfiction across multiple archives — AO3, FanFiction.net, FicAlley, and any user-supplied EPUB.

One search bar over a single index spanning multiple sites, with AO3-parity filters, a clean reader for stories hosted directly, and one-click import for fresh stories from any URL.

## What works today

### Search & discovery
- **Unified search** across AO3, FanFiction.net, FicAlley, HPFFA, and DLP-curated picks in one query
- **Operator syntax** in any order: `fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff`
- **Tag autocomplete** — fandom, relationship, character and tag filter inputs suggest real values from the index as you type (with story counts), backed by a precomputed facets table so it's instant even on millions of rows
- **Search-first discovery** — you don't need the Import tab to get fics: when a search returns few or no indexed results and AO3 is selected, the app auto-pulls a deeper live batch (and the no-results screen has a one-click "Search AO3 directly" button). The Import tab remains the full power-user control panel for bulk scrapes
- **Canonical-tag autocomplete in Import** — the Import tab's fandom fields autocomplete index-first then fall back to AO3's canonical tag list (`/api/stats/suggest-canonical`), so you can discover and correctly spell new fandoms to scrape, avoiding malformed-tag errors
- **Click-to-search tags** — every fandom, ship, character, freeform tag and warning is a link that pre-fills the corresponding filter
- **"Surprise me"** — random discovery on the landing page (real stories only, no drabbles/art), optionally scoped to your active fandom filter
- **Cross-site filter correctness** — fandom is matched strictly while secondary facets (character/ship/tag) and missing metadata (word count, status, rating, language) are matched permissively, so dump rows with sparse metadata surface correctly without flooding fandom searches
- **Cross-post de-duplication** — the same fic posted on AO3, FF.net, SquidgeWorld etc. is collapsed into a single result. New imports are deduped automatically (conservative title + author matching); a one-shot batch (`dedup-crossposts`) cleans up already-indexed data. The canonical copy keeps every site's link and hosts the most recently updated full text. Cards show a "+N copies" badge; detail pages list "Also on X" links for each version
- **AO3 deep filtered scrape** — paginated tag-works listing with full filter support, run as an async job with live progress. AO3's heavy endpoints are slow (5–20s) but reachable from a normal connection; the app uses generous granular timeouts (patient read, quick connect) and retries 525s on the same host before giving up
- **Five Harry Potter archives via AO3 Open Doors / Otwarchive**:
  - **HPFFA** — the ~85k-story HarryPotterFanfiction.com archive (collection `hpfanfiction_hpff`), tagged `hpffa_archive`
  - **HexFiles** — the separate ~18k-member Harry Potter FanFic Archive (collection `harrypotterfanficarchive`), tagged `hexfiles_archive`
  - **SquidgeWorld** — ~30k mostly-HP works; runs the same Otwarchive software as AO3, scraped directly from squidgeworld.org, tagged `squidgeworld_archive`
  - **DLP library** — DarkLordPotter's curated catalog of ~1000+ vetted HP fanfics, with DLP's curated tags merged on
  - **janelleshane seed** — a 112k-row metadata-only seed (titles/authors/summaries scraped with permission from AO3), imported from the command line as a broad discovery layer, tagged `janelleshane_seed`
- **AO3 Atom feeds** — per-canonical-tag feeds, polled on a 6h schedule plus on-load with auto-mirror fallback
- **Live AO3 fetch** on every search (3 pages = ~60 stories), results persisted so the index grows passively
- **FF.net discovery via Wayback Machine CDX** — FFN is Cloudflare-walled from VPS IPs, but archive.org's index isn't; we enumerate FFN URLs from Wayback and import on-demand
- **Fast paginated results** — result count capped at "5000+" for speed on large indexes; GIN indexes on the facet arrays keep fandom/tag filtering fast

### Reading & library
- **One-click "Import & Read"** — every search result for AO3/FFN with `is_hosted=false` shows an "Import & Read" button that fetches the full EPUB via FicHub and drops you into the reader
- **In-app reader** — Charter/Georgia serif (conventional italics), serif↔sans toggle, narrow↔wide column, A+/A−, **adjustable line spacing**, **light / sepia / dark themes**, scroll progress bar, ← → chapter navigation. Keyboard: `←/→` chapters, `+/−` text size, `↕` line spacing, `t` cycle theme
- **EPUB export / offline reading** — any hosted story has a `↓ EPUB` button that builds a valid EPUB 2 file on the fly (stdlib only, no dependencies) for reading offline in any e-reader app
- **In-app offline reading** — tap `⤓ Save offline` on any readable story to download its chapters into the browser's IndexedDB. The reader falls back to that saved copy when the network is unreachable (e.g. away from your Tailscale connection), an **Offline** tab in the library lists everything saved on the device, and a service worker caches the app shell so it boots with no connection. Pre-save on wifi, read anywhere
- **Similar stories** — every story detail page shows an "If you like this, try…" section, recommending reads by shared fandoms/ships/tags with overlap scoring (ships weighted highest, then fandom, then freeform tags, with a small popularity tiebreaker)
- **Scroll-position reading progress** — debounced save of chapter + scroll position; opening a chapter you've partly read jumps back to where you left off
- **iOS Books-style hosted library** — book covers with hashed gradients, hover lift, drop shadow. Each shows an amber progress bar across the bottom and `Ch N/M · X%` when you've started reading. Clicking deep-links to your saved chapter, not chapter 1
- **Continue Reading** — story detail page replaces "Read Chapter 1" with "Continue Chapter N · Start over" when progress exists
- **EPUB upload (single or bulk)** — drag/drop up to 100 .epub files; mobile-friendly file picker
- **Bulk URL import** — paste a list of AO3/FFN links (one per line) and import them all sequentially with a live progress bar and per-URL success/fail results
- **DLP badge & cross-post links** — DLP-curated stories show a purple "DLP" badge; cross-posted works show a "+N copies" badge in results and "Also on AO3 / FF.net / SquidgeWorld" links on the detail page

### Accounts & sync
- **Optional accounts** — username + password (bcrypt), no email required. 90-day httponly cookie sessions
- **Cross-device sync with merge** — bookmarks, reading progress, recents and settings sync to your account and **merge** across devices rather than overwriting: bookmarks union, progress keeps the most recently updated per story, recents union (capped). Using your phone and laptop together never silently drops data
- **Resilient sync engine** — dirty-key retry queue, request coalescing, re-sync on tab focus / network reconnect / 60s interval, and a `sendBeacon` flush on page unload
- **Account management** (`/account`) — active sessions list (see every signed-in device), change password (signs out other devices), sign out all other devices, delete account (password-confirmed, cascades)
- **Security** — login rate limiting (8 fails → 5-minute lock), opportunistic expired-session cleanup
- **Works signed-out too** — bookmarks, recents and progress still work locally in localStorage with no account

### Data seeds
- **HuggingFace FFN metadata dump** — 6.6M FFnet rows (IDs 1–10.9M, 2014-era). The single biggest free seed. Auto-downloads via `huggingface_hub` from inside the backend container. Uses Postgres `ON CONFLICT DO NOTHING` for idempotent batched inserts
- **Live AO3** filling the gap from 2021 onward
- **FicAlley** for offline HP archive with full text
- **FicHub** for any fresh per-URL fetch

### Settings & UX
- **Settings page** at `/settings` — tracked fandom, poll-on-load, live AO3 fetch, default sites/sort/per-page, feed filters, reader font and width, explicit visibility, and an advanced **direct-crawl toggle** (off by default). Persisted server-side
- **Direct crawl toggle** — opt-in scheduled crawling of AO3/FF.net. AO3 works from a normal home/residential connection (its heavy filtered-works and feed pages are just slow, ~5–20s, which the app now waits out with generous granular timeouts and same-host 525 retries). FF.net stays Cloudflare-blocked for direct server requests regardless of IP — use one-click FicHub URL import for it. Controlled at runtime from the DB setting (no restart); `GET /api/library/crawl-status` reports recent crawl outcomes
- **Index status widget** — per-site counts, total stories, total words, DLP and HPFFA counts
- **Fully responsive** — proper mobile viewport; on phones the filter sidebar becomes a slide-out drawer with a backdrop, an active-filter badge, and an Apply button (instead of being hidden). Tablet/phone/small-phone breakpoints, 40–44px touch targets, horizontally scrolling library tabs, 2-column book grid, full-width stacked actions. Works from any host over Tailscale/LAN
- **Loading skeletons** while results load, smooth scroll-to-top on page change
- **Keyboard shortcuts** — `/` focus search, `Esc` close help, `← →` navigate chapters, `+ −` resize reader, `↕` line spacing, `t` reader theme

## Stack

- **Backend** — FastAPI · SQLAlchemy · PostgreSQL 16 · APScheduler · httpx · BeautifulSoup4 · pyarrow · huggingface-hub
- **Frontend** — Next.js 15 (App Router, `/api/*` rewrite proxy to backend) · TypeScript · Tailwind base + custom editorial CSS
- **External services** — FicHub (cross-archive download API), Wayback Machine CDX, HuggingFace Hub
- **Data sources** — HuggingFace `mrzjy/fanfiction_meta` (6.6M FFN rows) · AO3 Atom feeds · AO3 tag-works deep-scrape · DLP library list · Wayback Machine FFN URL discovery · FicHub per-URL · FicAlley dump · uploaded EPUBs

## Deployment & accessing from another device

FicAtlas runs everything in Docker on one host (single VPS or homelab box). The frontend container also acts as a reverse-proxy for `/api/*` to the backend container — this is the architecture that makes phone access via Tailscale or LAN work.

```
┌─────────────────┐
│ phone / laptop  │  ← only sees port 3000
└────────┬────────┘
         │  http(s)://<host>:3000/...
         ↓
   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
   │ frontend :3000  │───▶│ backend :8000   │───▶│ postgres :5432  │
   │ (Next.js + RW)  │    │ (FastAPI)       │    │                 │
   └─────────────────┘    └─────────────────┘    └─────────────────┘
```

Only **port 3000** is reachable from clients. The frontend's `next.config.ts` declares a rewrite that forwards `/api/:path*` → `http://backend:8000/api/:path*`, so the browser only ever talks to one origin (no CORS, no port-8000 exposure, no env-var pinning). Tailscale, LAN, or a Cloudflare Tunnel pointed at port 3000 all work the same way.

The backend (8000) and Postgres (5432) are bound to `127.0.0.1` in `docker-compose.yml`, so they are reachable from the host but not from the LAN or the tailnet. This is deliberate: Postgres uses a default password, and the `/api/library/*` endpoints (delete hosted story, trigger scrapes) have no authentication, so neither port should be exposed to a network. The app is unaffected — the frontend reaches the backend over the internal compose network.

To access from your phone over Tailscale:
1. Install Tailscale on both the host and the phone, both signed into the same tailnet
2. On the phone, open `http://<server-tailscale-hostname>:3000`

## Quick start

```bash
git clone https://github.com/Georgexzy/ficatlas.git
cd ficatlas
docker compose up --build
docker compose exec backend python init_db.py
```

Open <http://localhost:3000>. API docs at <http://localhost:8000/docs> (from the host only).

### Search performance

Search is served by Postgres indexes that `init_db.py` creates:

- **Free text** matches a GIN `tsvector` (`ix_stories_doc_fts`) covering title, summary, author, fandoms, characters, relationships and tags. Because it goes through `websearch_to_tsquery`, quoted `"phrases"`, `or`, and `-negation` work in the free-text portion of a query.
- **Facet filters** (fandom/ship/character/tag) use substring matching so `fandom: Harry Potter` still matches AO3's canonical `Harry Potter - J. K. Rowling`. Only a trigram index can serve a leading-wildcard `LIKE`, so each of those columns has a `gin_trgm_ops` index over an `IMMUTABLE` array-to-text wrapper (`fic_arr`).

`api/search.py` builds its predicates from exactly these expressions. **If you change one side, change the other** — Postgres only uses an expression index when the query expression matches it, and a mismatch silently reverts search to a full sequential scan of every row rather than failing loudly.

## Importing data

### FicAlley (≈30k Harry Potter stories with full text)

```bash
# Copy the FicAlley pg_dump folder into the db container:
docker cp /path/to/faarchive ficatlas-db-1:/tmp/dump

# Restore into a temp database:
docker compose exec -e PGPASSWORD=ficatlas db bash -c '
  psql -U ficatlas -d postgres -c "DROP DATABASE IF EXISTS ficalley_tmp;"
  psql -U ficatlas -d postgres -c "CREATE DATABASE ficalley_tmp;"
  psql -U ficatlas -d postgres -c "DO \$\$ BEGIN CREATE ROLE frank; EXCEPTION WHEN OTHERS THEN NULL; END \$\$;"
  pg_restore -U ficatlas -d ficalley_tmp --no-owner --no-acl /tmp/dump
'

# Run the importer:
docker compose exec backend python fictionalley_importer.py
```

### AO3 metadata dump (~14M works) — the richest source

`trentmkelly/archiveofourown-meta` on HuggingFace: a 7.4GB ungated JSONL dump of
AO3 work metadata. This is the most valuable seed in the project, because it is
the only bulk source that carries **characters, relationships and freeform tags**.
Without it, 99.7% of indexed stories had no relationship data and 98.8% no
character data, which is what made those filters useless. Work IDs give real,
clickable AO3 URLs.

The download is resumable and the import is idempotent (`ON CONFLICT DO NOTHING`),
so it is safe to interrupt and re-run.

```bash
# preview first:
docker compose exec backend python ao3_meta_importer.py --download --limit 20 --dry-run
# full import (downloads once to data/ao3_meta/, then streams):
docker compose exec backend python ao3_meta_importer.py --download
# resume mid-file if you stopped it:
docker compose exec backend python ao3_meta_importer.py --skip 4000000
```

### Harry Potter metadata seed (janelleshane, 112k works)

A broad titles/authors/summaries seed scraped with permission from AO3. Metadata
only — no full text, and no source URL, so these rows link out to an AO3 search
for the title and author rather than to a work page. Useful as a discovery layer.
Rows are matched against any existing copies so it won't duplicate indexed stories.

```bash
docker compose exec backend python janelleshane_importer.py --download
# preview first:
docker compose exec backend python janelleshane_importer.py --download --limit 500 --dry-run
```

### HP archives (in-app, no CLI)

The Library page has one-click buttons for several Harry Potter archives that run
as background jobs: **HPFFA**, **HexFiles**, **SquidgeWorld**, and **DLP**. Each
tags its imports for later filtering. After a big import, hit **Merge cross-posted
duplicates** on the same page to collapse multi-site copies into single results.

### Live & user-driven imports

For newer stories not in the dumps, two paths:

- **Live AO3 fetch**: every search automatically pulls and indexes up to 60 fresh AO3 results. Use the "↻ Refresh from AO3" button on the results page to force a deeper fetch (5 pages) for the current query.
- **URL paste**: paste any AO3 or FF.net URL into the search bar. A banner appears with a one-click import. The full text is pulled via FicHub.
- **Bulk URL import**: paste a whole list of URLs (one per line) in the Library page; each is imported via FicHub with a live progress bar.
- **EPUB upload**: drag one or many .epub files onto the import zone in the Library page. Bulk uploads process up to 100 files at a time with a progress bar.

## Search syntax

| Example | Meaning |
|---------|---------|
| `harry potter slow burn` | Free text across title/summary/fandoms/tags/author |
| `fandom: Harry Potter` | Filter — unquoted multi-word |
| `fandom:"Harry Potter"` | Quoted equivalent |
| `ship:Draco/Hermione` | Relationship (also `pairing:`, `rel:`) |
| `char: Hermione Granger` | Character |
| `tag: slow burn` | Additional tag |
| `rating:M` | G / T / M / E / NR |
| `status:complete` | complete / wip / ongoing |
| `>100k` `<50k` `100k-200k` | Word count shorthand |
| `wc:>100k` `words:200k+` | Word count operator |
| `updated:1y` `since:2024` | Date filters |
| `lang:French` | Language |
| `site:ao3` | Restrict to one site |
| `-tag:fluff` | Exclude (prefix any operator with `-`) |
| `complete` `wip` `mature` | Standalone status/rating words |
| `https://archiveofourown.org/works/12345` | Paste a URL to import the story |

Free text runs through Postgres `websearch_to_tsquery`, so `"exact phrase"`, `or`,
and `-word` work in the non-operator part of a query too.

### How filters treat missing metadata

Filters are **strict**: a story matches only if it actually carries the value. This
matters because the bulk sources are uneven — the FFN dump has fandom, rating and
word count but no characters or ships. Previously a story with no data for a field
matched *any* filter on it, so filtering by a ship returned millions of stories
with no ship at all.

Tick **"Include stories with missing info"** in the sidebar (or pass
`include_unknown=true`) to widen a search back to rows whose metadata was never
captured.

### Character and ship aliases

Archives name the same people differently: FictionAlley writes `D/Hr`, AO3 writes
`Hermione Granger/Draco Malfoy`, and a reader types `Draco/Hermione`.
`backend/character_aliases.py` maps between them, so all three find the same
stories. Aliases are matched as whole tag values, never substrings — several codes
are a single letter (`H`, `D`, `R`) that would otherwise match almost every row.

Romantic (`/`) and platonic (`&`) pairings stay distinct: `Draco/Hermione` does not
return works tagged `Draco & Hermione`. Names outside the alias table fall back to
substring matching, so other fandoms behave as before.

## Architecture

```
┌─────────────────────────────────────┐
│  Next.js frontend (port 3000)       │
│  Search · Reader · Library          │
└──────────────┬──────────────────────┘
               │ /api
┌──────────────▼──────────────────────┐
│  FastAPI backend (port 8000)        │
│  search · stories · library · stats │
│  + crawl scheduler (APScheduler)    │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  PostgreSQL 16                      │
│  stories · chapters · crawl_jobs    │
└─────────────────────────────────────┘
```

Bulk indexing is one-time per source via the importers. Day-to-day, the live-fetch module hits AO3's search pages on demand for freshness and persists results into the DB. FicHub bridges Cloudflare for AO3/FFnet imports.

## API endpoints

- `GET  /api/search` — main search endpoint with all filters
- `GET  /api/search/random` — random discovery ("Surprise me")
- `GET  /api/stories/{id}` — story detail + chapter list
- `GET  /api/stories/{id}/chapters/{n}` — chapter content for reader
- `GET  /api/stories/{id}/export.epub` — download a hosted story as EPUB
- `GET  /api/stories/{id}/similar` — recommended similar stories
- `POST /api/library/upload-epub` — multipart EPUB upload (single file)
- `POST /api/library/upload-epubs` — bulk EPUB upload (up to 100 files, batched)
- `POST /api/library/import-url` — fetch a URL via FicHub and host it
- `POST /api/library/discover-ao3` — async deep AO3 tag-works scrape (poll `/api/library/jobs/{id}`)
- `POST /api/library/discover-hpffa` — async HPFFA scrape via AO3 Open Doors collection
- `POST /api/library/discover-hexfiles` — async HexFiles (Harry Potter FanFic Archive) scrape via AO3 Open Doors
- `POST /api/library/discover-squidgeworld` — async SquidgeWorld scrape (Otwarchive software)
- `POST /api/library/discover-ffnet` — enumerate FF.net URLs via the Wayback CDX index
- `POST /api/library/discover-dlp` — scrape DarkLordPotter's curated library list
- `POST /api/library/dedup-crossposts` — async batch that merges cross-posted duplicates already in the index
- `GET  /api/library/ao3-status` / `POST /api/library/admin/clear-ao3-cooldown` — AO3 block cooldown state/reset
- `GET  /api/library/hosted` · `DELETE /api/library/hosted/{id}` — manage hosted stories
- `GET/POST /api/settings` — read or update runtime settings
- `GET  /api/stats/sites` · `GET /api/stats/totals` — index counts and totals
- `GET  /api/stats/suggest?kind=&q=` — tag autocomplete · `POST /api/stats/refresh-facets` — rebuild autocomplete index
- `POST /api/auth/signup` · `/login` · `/logout` · `GET /api/auth/me` — authentication
- `POST /api/auth/change-password` · `/logout-all` · `/delete-account` · `GET /api/auth/sessions` — account management
- `GET /api/userdata` · `PUT/DELETE /api/userdata/{key}` · `POST /api/userdata/merge` — per-account synced storage

## Known limitations

- **FF.net live search and bulk crawling from cloud IPs** — FanFiction.net runs an aggressive interactive Cloudflare challenge that blocks server-side scraping from datacenter IPs entirely. URL-based import works through FicHub (which solves the challenge on its end). For bulk freshness you'd need a residential-proxy browser API.
- **AO3 latency, not blocking** — from a normal residential connection AO3 is reachable, but its filtered-works and atom-feed endpoints are slow to generate (≈7s typical, spiking to 15–20s under load) and intermittently return Cloudflare 525s when their origin is overloaded. The app handles this with generous granular timeouts, same-host 525 retries with backoff, and a brief self-cooldown only after many consecutive failures (not a single slow response). On a datacenter IP AO3 may block outright (525/timeouts on everything) — a Tailscale exit node or WARP routes around that. The HuggingFace dump, FicHub per-URL import, DLP, and FicAlley remain the fastest bulk paths.

## Acknowledgements

- **AO3** — for publishing the official data dump
- **FicHub** — for the cross-archive download API that bypasses Cloudflare cleanly
- **Internet Archive** — for preserving FanFiction.net
- The unofficial **FicAlley archive maintainers** — for keeping the dead site alive in pg_dump form

## Status

Personal project, not a finished product. Things listed under "What works today" do work; everything else is aspirational.
