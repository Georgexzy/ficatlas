# FicAtlas

Fanfiction discovery platform — search AO3, FF.net, and more in one place.

## Stack

| Layer     | Tech |
|-----------|------|
| Frontend  | Next.js 15, TypeScript, Tailwind CSS |
| Backend   | Python, FastAPI |
| Database  | PostgreSQL 16 |
| Crawlers  | httpx + BeautifulSoup4 |

## Project Structure

```
ficatlas/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── api/
│   │   ├── search.py        # Unified search endpoint (full AO3-parity filters)
│   │   ├── stories.py       # Story detail endpoint
│   │   └── crawl.py         # Crawl trigger + job management
│   ├── models/
│   │   └── story.py         # SQLAlchemy models (Story, CrawlJob)
│   ├── db/
│   │   └── session.py       # Database session management
│   ├── crawlers/
│   │   ├── base.py          # BaseCrawler (rate limiting, upsert, retries)
│   │   ├── ao3.py           # AO3 adapter
│   │   └── ffnet.py         # FF.net adapter
│   ├── init_db.py           # One-time DB setup + index creation
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── page.tsx         # Main search UI (sidebar filters + results)
│   │   ├── layout.tsx
│   │   └── globals.css      # Dark editorial theme
│   └── lib/
│       ├── types.ts         # TypeScript types
│       └── api.ts           # API client + formatters + constants
└── docker-compose.yml
```

## Quick Start

### With Docker (recommended)

```bash
docker-compose up
```

Then open http://localhost:3000

### Manual

**Database**
```bash
createdb ficatlas
DATABASE_URL=postgresql://localhost/ficatlas python backend/init_db.py
```

**Backend**
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
# App at http://localhost:3000
```

## API

### Search
```
GET /api/search
```

**Include filters**
| Param | Type | Description |
|-------|------|-------------|
| `q` | string | Free-text search |
| `sites` | csv | `ao3,ffnet,wattpad` |
| `fandoms` | csv | Include fandoms |
| `characters` | csv | Include characters |
| `relationships` | csv | Include pairings |
| `tags` | csv | Include additional tags |
| `ratings` | csv | `G,T,M,E,NR` |
| `warnings` | csv | Include archive warnings |
| `categories` | csv | `F/F,F/M,M/M,Gen,Multi,Other` |
| `crossovers` | string | `include` (default) / `exclude` / `only` |

**Exclude filters** — same fields prefixed `exclude_`

**More options**
| Param | Type | Description |
|-------|------|-------------|
| `status` | csv | `complete,in_progress,abandoned` |
| `language` | string | e.g. `English` |
| `word_count_min` | int | Minimum word count |
| `word_count_max` | int | Maximum word count |
| `updated_after` | date | ISO date e.g. `2024-01-01` |
| `updated_before` | date | ISO date |
| `published_after` | date | ISO date |
| `explicit` | bool | Show explicit content (default false) |
| `search_within` | string | Narrow within current results |

**Pagination & sort**
| Param | Values |
|-------|--------|
| `sort` | `relevance`, `updated_desc`, `published_desc`, `kudos_desc`, `hits_desc`, `bookmarks_desc`, `comments_desc`, `word_count_desc`, `word_count_asc` |
| `page` | int (default 1) |
| `per_page` | int 1–100 (default 20) |

### Trigger a crawl
```
POST /api/crawl/trigger/ao3?job_type=incremental
POST /api/crawl/trigger/ffnet?job_type=full
```

## Crawler notes

- **AO3**: 5s delay between requests. Crawls works pages. AO3 has no API so this is HTML scraping — be respectful of their infrastructure.
- **FF.net**: 3s delay. Scrapes category listing pages which contain full story metadata.
- Both crawlers upsert — re-running is safe.
- Add crawlers for Wattpad/RoyalRoad by extending `BaseCrawler`.

## Database

Key indexes:
- GIN indexes on `fandoms`, `tags`, `relationships`, `characters` (array overlap queries)
- Full-text search vector on `title + summary + author`
- Partial index on `updated_at` excluding explicit content
- Composite unique index on `(site, site_id)`
