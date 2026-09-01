# Deep review — coverage

One row per area of the site. The review takes the **oldest** date, unless the
current diff touches an area, in which case that one goes first.

"Covered" means: all of its code read, its main paths exercised against the live
system, and every fault either fixed or consciously accepted with a reason. Not
"skimmed".

| Area | Code | Last reviewed | State |
|---|---|---|---|
| Search — query path | `api/search.py`, `query_parser.py`, `frontend/lib/queryParser.ts`, `search_cache.py` | 2026-09-01 | Ship-alias resolution reviewed and fixed across 4 passes. Rest of the path not read in full. |
| Search — ranking & relevance | `api/search.py` relevance block, `popularity_rank.py` | 2026-09-01 | Ship bonus verified reaching page 1. Weights and `is_category` not re-examined. |
| Series | `ao3_series.py`, `ao3_series_fill.py`, `series_detect.py`, `frontend/lib/seriesNote.ts`, `series/[id]` | 2026-09-01 | Fill loop and the completeness note fixed. `plausible_position` role demotion open by choice. |
| Traffic & admin | `tracking.py`, `api/traffic.py`, `cloudflare_analytics.py`, `frontend/app/admin/*` | 2026-09-01 | Covered. Write path, all five read endpoints and the length limits exercised against the live DB. Fixed: shell-vs-.env credential precedence. Open: edge cache ratio 6.7% (needs its own design — see report). |
| Hubs & SEO | `hub_build.py`, `fandom_hubs.py`, `ship_hubs.py`, `api/hubs.py`, `indexnow.py`, sitemap, robots | — | Never. |
| Auth, sessions & permissions | `api/auth.py`, `api/password_reset.py`, `author_permission.py`, `ratelimit.py` | 2026-09-01 | PARTIAL. Fixed: 8 plaintext session tokens hashed in place; lockout made worker-aware. Sound: view-as downgrade-only, reset tokens hashed/TTL/single-use, COOKIE_SECURE true in prod. NOT read: `author_permission.py` (444 lines), the signup/email/delete-account endpoints. |
| Reader, offline & library | `frontend/lib/offline.ts`, reader routes, `api/library.py` | — | Never. `IMPROVEMENTS.md` P0 (silent eviction of saved stories) may still be open. |
| Import & enrichment | `ao3_*.py`, `ffnet_*.py`, `fictionalley_importer.py`, `live_fetch/*` | — | Never. Touches the index destructively. |
| Worker loops & scheduling | `worker.py`, `scheduler.py`, `ao3_budget.py` | 2026-09-01 | Only the two loops changed this session. The other ~16 not read. |
| Deploy & ops | `deploy/*`, `backup.sh`, `watchdog.sh`, `autotune.sh` | 2026-09-01 | Ring 4 verified. Scripts themselves not read. |
| Frontend shell & UX | `app/layout.tsx`, `app/page.tsx`, nav, filters, `globals.css` | 2026-09-01 | PARTIAL. Fixed: story pages server-rendered (323 → 2,175 chars, `h1` present), site-wide skip link, `<main>` on story/series/ship/fandom, hover-prefetch on pagination. Open: `<main>` still absent on /library, /account, /settings; 99 raw fetches with 10 guarded and `fetchOrFail` still unused (non-search pages — core search has its own timeout); no `loading.tsx` anywhere; `app/page.tsx` is 2,853 lines and not read in full; hover interaction unverified without browser tooling. |
