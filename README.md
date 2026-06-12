# FicAtlas

A unified search engine for fanfiction across multiple archives — AO3, FanFiction.net, FicAlley, and any user-supplied EPUB.

One search bar over a single index spanning multiple sites, with AO3-parity filters, a clean reader for stories hosted directly, and one-click import for fresh stories from any URL.

## What works today

- **Unified search** across AO3, FanFiction.net, and FicAlley with one query bar
- **Operator syntax** in any order: `fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff`
- **Quoted or unquoted multi-word values** — `fandom: Harry Potter` reads as the full phrase
- **Live AO3 fetch** — each search pulls fresh AO3 results (3 pages = ~60 stories per search). These are persisted into the DB so the index grows organically every time anyone searches.
- **AO3 Atom feed discovery** — the reliable fresh-data path. AO3 publishes per-canonical-tag Atom feeds (`/tags/{tag}/feed.atom`) that aren't rate-limited like search pages. FicAtlas polls these on a schedule for tracked fandoms and auto-indexes new works, with the OTW mirror (`archive.transformativeworks.org`) as a fallback on origin errors. Trigger manually from Library → Import → "Discover fresh AO3 works", or let the scheduler poll every 6h.
- **URL-paste import** — paste any AO3 or FanFiction.net URL into the search bar and a banner appears with a one-click "Import" button. Pulls the full text via FicHub, bypassing Cloudflare entirely. Works for both sites.
- **EPUB upload (single or bulk)** — drag and drop one or many .epub files into the library. Up to 100 at a time, each becomes a hosted, searchable, readable story.
- **Remove hosted stories** — delete any imported or uploaded story (and its stored text) from the library with one click. The bulk-indexed archive can't be deleted this way, only your own hosted additions.
- **Settings page** — a dedicated `/settings` route to configure the tracked fandom, auto-pull-on-load, live AO3 fetch, default search sites/sort/page-size, explicit visibility, and reader font (serif/sans) and width (narrow/wide). Persisted server-side across restarts.
- **Reader** — serif or sans typography, narrow or wide column, adjustable text size, a top reading-progress bar, ← → chapter navigation, and auto-saved reading position.
- **Refresh from AO3** — button on results page triggers a 5-page deep fetch for the current query and adds new stories to the index
- **In-app reader** for any hosted story (FicAlley stories, FicHub imports, EPUB uploads). Serif typography, ← → chapter nav, A+/A− font sizing, auto-saved reading progress.
- **Bookmarks, recents, reading progress** — all client-side in localStorage, no account needed
- **Keyboard shortcuts** — `/` to focus search, `Esc` to close help, `← →` to navigate chapters, `+ −` to resize reader text
- **Index status widget** — header button shows per-site counts and growth over time
- **Wayback fallback** — every FicAlley story card includes a Wayback Machine link as backup since the original site is offline

## Stack

- **Backend** — FastAPI · SQLAlchemy · PostgreSQL 16 · APScheduler · httpx · BeautifulSoup4
- **Frontend** — Next.js 15 · TypeScript · Tailwind base + custom editorial CSS
- **External services** — FicHub (cross-archive download API)
- **Data sources** — AO3 2021 official CSV dump · FanFiction.net 2015 Archive.org SQLite · Unofficial FicAlley pg_dump · Live fetching for freshness

## Quick start

```bash
git clone https://github.com/Georgexzy/ficatlas.git
cd ficatlas
docker compose up --build
docker compose exec backend python init_db.py
```

Open <http://localhost:3000>. API docs at <http://localhost:8000/docs>.

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

### AO3 (2021 official dump, metadata for ~5M works)

```bash
docker compose exec backend python ao3_dump_importer.py --fandom "Harry Potter" --limit 50000
# or full import:
docker compose exec backend python ao3_dump_importer.py
```

### FanFiction.net (2015 Archive.org SQLite dump)

```bash
docker compose exec backend python ffnet_sqlite_importer.py --download
```

### Live & user-driven imports

For newer stories not in the dumps, two paths:

- **Live AO3 fetch**: every search automatically pulls and indexes up to 60 fresh AO3 results. Use the "↻ Refresh from AO3" button on the results page to force a deeper fetch (5 pages) for the current query.
- **URL paste**: paste any AO3 or FF.net URL into the search bar. A banner appears with a one-click import. The full text is pulled via FicHub.
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
- `GET  /api/stories/{id}` — story detail + chapter list
- `GET  /api/stories/{id}/chapters/{n}` — chapter content for reader
- `POST /api/library/upload-epub` — multipart EPUB upload (single file)
- `POST /api/library/upload-epubs` — bulk EPUB upload (up to 100 files, batched)
- `POST /api/library/import-url` — fetch a URL via FicHub and host it
- `POST /api/library/refresh-ao3` — wider live fetch for a query
- `GET  /api/library/can-import?url=…` — check if a URL is importable
- `POST /api/library/poll-feed` — poll an AO3 canonical-tag Atom feed and index new works
- `GET  /api/library/hosted` — list stories hosted on FicAtlas (imports + uploads)
- `DELETE /api/library/hosted/{id}` — remove a hosted story and its chapters
- `GET/POST /api/settings` — read or update runtime settings (DB-backed key/value)
- `GET  /api/stats/sites` — per-site indexed counts
- `GET  /api/stats/totals` — index totals (stories, hosted, words)
- `GET  /api/crawl/jobs` — recent crawl jobs
- `GET  /api/crawl/schedule` — crawl scheduler state

## Known limitations

- **FF.net live search and bulk crawling from cloud IPs** — FanFiction.net runs an aggressive interactive Cloudflare challenge that blocks server-side scraping from datacenter IPs entirely. There is no clean server-side path. URL-based import works through FicHub (which solves the challenge on its end). For bulk freshness you'd need a residential-proxy browser API.
- **AO3 direct scraping** — AO3 doesn't use an interactive challenge; it rate-limits by IP and request rate, returning 418/429 when throttled and 525/503 when its own origin is overloaded (common through 2025 due to infrastructure strain). The fix isn't a Cloudflare "bypass" — it's politeness: a `bot` user-agent, request delays, weekday off-peak scheduling, and the mirror-domain fallback. FicAtlas uses AO3's **Atom feeds** rather than scraping search pages, since feeds aren't throttled the same way. Direct crawlers are disabled by default (`ENABLE_DIRECT_CRAWL=false`).
- **No accounts** — bookmarks, recents, and reading progress are localStorage only.

## Acknowledgements

- **AO3** — for publishing the official data dump
- **FicHub** — for the cross-archive download API that bypasses Cloudflare cleanly
- **Internet Archive** — for preserving FanFiction.net
- The unofficial **FicAlley archive maintainers** — for keeping the dead site alive in pg_dump form

## Status

Personal project, not a finished product. Things listed under "What works today" do work; everything else is aspirational.
