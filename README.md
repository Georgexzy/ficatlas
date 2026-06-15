# FicAtlas

A unified search engine for fanfiction across multiple archives — AO3, FanFiction.net, FicAlley, and any user-supplied EPUB.

One search bar over a single index spanning multiple sites, with AO3-parity filters, a clean reader for stories hosted directly, and one-click import for fresh stories from any URL.

## What works today

### Search & discovery
- **Unified search** across AO3, FanFiction.net, FicAlley, and DLP-curated picks in one query
- **Operator syntax** in any order: `fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff`
- **Click-to-search tags** — every fandom, ship, character, freeform tag and warning is a link that pre-fills the corresponding filter
- **AO3 deep filtered scrape** — `/api/library/discover-ao3` paginates AO3's tag-works listing with full filter support (min/max words, complete-only, sort by recent/kudos/words/hits/popularity, exclude tags). Up to 20 pages × 20 works = ~400 matching stories per call, with polite 3s delays
- **AO3 Atom feeds** — per-canonical-tag feeds (`/tags/{N}/feed.atom`), polled on a 6h schedule plus on-load with auto-mirror fallback. Filterable in Settings (min words, max words, complete-only)
- **Live AO3 fetch** on every search (3 pages = ~60 stories), results persisted so the index grows passively
- **FF.net discovery via Wayback Machine CDX** — FFN is Cloudflare-walled from VPS IPs, but archive.org's index isn't; we enumerate FFN URLs from Wayback and import on-demand
- **DLP library import** — scrapes DarkLordPotter's curated catalog of ~1000+ vetted HP fanfics, merges DLP's curated tags onto each imported story

### Reading & library
- **One-click "Import & Read"** — every search result for AO3/FFN with `is_hosted=false` shows an "Import & Read" button that fetches the full EPUB via FicHub and drops you into the reader
- **In-app reader** — Charter/Georgia serif (conventional italics), serif↔sans toggle, narrow↔wide column, A+/A−, scroll progress bar, ← → chapter navigation
- **Scroll-position reading progress** — debounced save of chapter + scroll position; opening a chapter you've partly read jumps back to where you left off
- **iOS Books-style hosted library** — book covers with hashed gradients, hover lift, drop shadow. Each shows an amber progress bar across the bottom and `Ch N/M · X%` when you've started reading. Clicking deep-links to your saved chapter, not chapter 1
- **Continue Reading** — story detail page replaces "Read Chapter 1" with "Continue Chapter N · Start over" when progress exists
- **EPUB upload (single or bulk)** — drag/drop up to 100 .epub files; mobile-friendly file picker (iOS Files surfaces EPUBs via the broadened accept attribute)
- **DLP badge & cross-post links** — DLP-curated stories show a purple "DLP" badge; if a story has FFN/AO3 cross-posts recorded, both buttons appear on the detail page
- **Bookmarks, recents, reading progress** — client-side in localStorage, no account needed

### Data seeds
- **HuggingFace FFN metadata dump** — 6.6M FFnet rows (IDs 1–10.9M, 2014-era). The single biggest free seed. Auto-downloads via `huggingface_hub` from inside the backend container. Uses Postgres `ON CONFLICT DO NOTHING` for idempotent batched inserts
- **Live AO3** filling the gap from 2021 onward
- **FicAlley** for offline HP archive with full text
- **FicHub** for any fresh per-URL fetch

### Settings & UX
- **Settings page** at `/settings` — tracked fandom, poll-on-load, live AO3 fetch, default sites/sort/per-page, feed filters (min/max words, complete-only), reader font and width, explicit visibility. Persisted server-side
- **Index status widget** — per-site counts, total stories, total words, DLP-curated count
- **Mobile-responsive** — card actions wrap on small screens, story detail actions stack, drop target taller; works from any host (see Deployment below)
- **Keyboard shortcuts** — `/` focus search, `Esc` close help, `← →` navigate chapters, `+ −` resize reader

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

Only **port 3000** needs to be reachable from clients. The frontend's `next.config.ts` declares a rewrite that forwards `/api/:path*` → `http://backend:8000/api/:path*`, so the browser only ever talks to one origin (no CORS, no port-8000 exposure, no env-var pinning). Tailscale, LAN, or a Cloudflare Tunnel pointed at port 3000 all work the same way.

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
- `POST /api/library/discover-ffnet` — enumerate FF.net URLs via the Wayback CDX index
- `POST /api/library/discover-dlp` — scrape DarkLordPotter's curated library list (HP or other fandoms)
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
