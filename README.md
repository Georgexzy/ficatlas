# FicAtlas

A unified search engine for fanfiction across archives — AO3, FanFiction.net, FicAlley, and more.

One search bar over a single index spanning multiple sites, with AO3-parity filters and a clean reader for stories we host directly.

## What works today

- **Unified search** across all indexed sites with one query bar
- **Operator syntax** in any order: `fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff`
- **Quoted or unquoted multi-word values**: `fandom: Harry Potter` reads as the full phrase
- **Live AO3 fetch** — each search also pulls fresh AO3 results on demand, even though the scheduled crawler is Cloudflare-blocked from cloud IPs
- **In-app reader** for FicAlley stories (we host the full text from the May 2021 dump)
- **EPUB upload + URL import** via FicHub — paste any AO3 or FFnet URL to import it as a hosted, readable story
- **Bookmarks, recents, reading progress** — all client-side in localStorage, no account needed
- **Keyboard shortcuts** — `/` to focus search, `Esc` to close help, `← →` to navigate chapters

## Stack

- **Backend** — FastAPI + SQLAlchemy + PostgreSQL 16 + APScheduler. Docker.
- **Frontend** — Next.js 15, TypeScript, Tailwind. Custom CSS for the editorial dark theme.
- **Data sources** — AO3 official CSV dump (2021), FFnet Archive.org SQLite (2015), unofficial FicAlley pg_dump (2021), FicHub API for on-demand fresh imports.

## Quick start

```bash
git clone https://github.com/Georgexzy/ficatlas.git
cd ficatlas
docker compose up --build
docker compose exec backend python init_db.py
```

Then open <http://localhost:3000>. API docs at <http://localhost:8000/docs>.

## Importing data

**FicAlley** (~30k Harry Potter stories with full text):

```bash
# 1. Copy the FicAlley pg_dump folder into the db container:
docker cp /path/to/faarchive ficatlas-db-1:/tmp/dump

# 2. Restore into a temp database:
docker compose exec -e PGPASSWORD=ficatlas db bash -c '
  psql -U ficatlas -d postgres -c "DROP DATABASE IF EXISTS ficalley_tmp;"
  psql -U ficatlas -d postgres -c "CREATE DATABASE ficalley_tmp;"
  psql -U ficatlas -d postgres -c "DO \$\$ BEGIN CREATE ROLE frank; EXCEPTION WHEN OTHERS THEN NULL; END \$\$;"
  pg_restore -U ficatlas -d ficalley_tmp --no-owner --no-acl /tmp/dump
'

# 3. Run the importer:
docker compose exec backend python fictionalley_importer.py
```

**AO3** (2021 official dump, metadata for ~5M works):

```bash
docker compose exec backend python ao3_dump_importer.py --fandom "Harry Potter" --limit 50000
# or full:
docker compose exec backend python ao3_dump_importer.py
```

**FanFiction.net** (2015 Archive.org SQLite dump):

```bash
docker compose exec backend python ffnet_sqlite_importer.py --download
```

## Search syntax

| Example | Meaning |
|---------|---------|
| `harry potter slow burn` | Free text across title/summary/fandoms/tags |
| `fandom: Harry Potter` | Filter — unquoted multi-word |
| `fandom:"Harry Potter"` | Quoted equivalent |
| `ship:Draco/Hermione` | Relationship (also `pairing:`, `rel:`) |
| `char: Hermione Granger` | Character |
| `tag: slow burn` | Additional tag |
| `rating:M` | G / T / M / E / NR |
| `status:complete` | complete / wip / ongoing |
| `>100k` `<50k` `100k-200k` | Word count shorthand |
| `updated:1y` `since:2024` | Date filters |
| `lang:French` | Language |
| `site:ao3` | Restrict to one site |
| `-tag:fluff` | Exclude (prefix any operator with `-`) |
| `complete` `wip` `mature` | Standalone status/rating words |

## Architecture

```
┌─────────────────────────────────────┐
│  Next.js frontend (port 3000)       │
│  Search · Reader · Library          │
└──────────────┬──────────────────────┘
               │ /api
┌──────────────▼──────────────────────┐
│  FastAPI backend (port 8000)        │
│  search · stories · stats · library │
│  crawl scheduler (APScheduler)      │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  PostgreSQL 16                      │
│  stories · chapters · crawl_jobs    │
└─────────────────────────────────────┘
```

Bulk indexing is one-time per source via the importers. Day-to-day, the live-fetch module hits archive search pages on demand so new fic shows up in results without waiting for a crawl. FicHub bridges Cloudflare for AO3/FFnet downloads, so users can import any fic into their library by URL.

## Known limitations

- **FF.net live search and scheduled crawl** — Cloudflare blocks server-side scraping from cloud IPs entirely. URL-based import works (via FicHub).
- **AO3 scheduled crawl** — also Cloudflare-blocked. Live single-page search works fine.
- **No accounts** — bookmarks/recents/progress are localStorage only.

## Acknowledgements

- AO3 — for publishing the official data dump
- FicHub — for the cross-archive download API
- Internet Archive — for preserving FFnet
- The unofficial FicAlley archive maintainers — for keeping the dead site alive in pg_dump form
