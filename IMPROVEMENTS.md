# FicAtlas — audit findings and improvement plans

Audited 2026-08-09 against `main` @ `dcd2dab` plus the uncommitted reader /
offline / search work from the same session. Covers the frontend, the backend
API, the service worker and the live deployment.

Two inputs beyond the code: measurements taken against the running stack (search
latency, Postgres cache behaviour, container CPU), and published user discussion
about comparable fanfiction readers — what people actually complain about — which
is cited where it motivates a priority rather than being taken as technical fact.

---

## 1. The structural finding

`lib/errors.ts` defines `fetchOrFail`: a fetch with a timeout, an abort, and
failure classification into the eight kinds the UI knows how to word. It is
careful, well documented, and **never called anywhere in the codebase.** There
are 79 raw `fetch(` calls across `app/` and `lib/`, and outside the files fixed
this session essentially none of them has a timeout or an abort.

This matters because it reframes "the site handles losing contact with the index
badly". It is not that the failure handling was never designed — it was designed
well and then not adopted. The remedy is adoption, not invention, which makes it
a much smaller and safer job than it looks.

Per-page breakdown of `fetch` calls vs. any abort/timeout handling:

| Page      | fetches | guarded |
|-----------|--------:|--------:|
| library   |      23 |       2 |
| account   |       6 |       0 |
| settings  |       5 |       0 |
| admin     |       2 |       0 |
| series    |       1 |       0 |

The same shape appeared in the reader and the story page before this session's
fixes: an unguarded fetch means an unreachable backend produces a spinner that
never resolves, and the only escape a reader has is to reload — which fires the
identical request again. That is how a struggling index becomes a hammered one.

---

## 2. Findings by severity

### P0 — silent data loss

**Saved stories can be evicted without warning.** `lib/offline.ts` writes whole
stories to IndexedDB and never calls `navigator.storage.persist()`. Browsers
evict origin storage under pressure on an LRU basis, and on iOS a PWA that has
not been added to the home screen is subject to a 7-day eviction window. So a
reader can save a long fic, lose connectivity a fortnight later, and find it
gone — with no indication that it was ever at risk.

This is not hypothetical. It is one of the most common complaints about the
existing unofficial AO3 reader apps: users report losing entire downloaded
libraries, and report that reinstalling to fix other bugs destroys everything
saved. It is also the failure most corrosive to trust, because the feature's
whole promise is "this will be here when the network isn't."

Nothing checks `navigator.storage.estimate()` before a download either, so
saving a very long work can fail partway on a device that is nearly full.

### P1 — correctness under more than one process

Four separate pieces of state are held in module-level Python dicts, each with a
comment reasoning explicitly from "there is one API container":

- `ratelimit.py` — the per-IP counter
- `api/auth.py` — the per-username login lockout
- `api/search.py` — `_live_last_run` / `_live_in_flight`, the live-fetch dedup
- `api/stats.py` — the totals and sites caches

`docker-compose.prod.yml` runs `uvicorn --workers 2`. Two workers means two
copies of every one of those. The consequences are ordered by how much they
matter:

1. **Rate limits become 2x their stated value**, because a client's requests are
   spread across workers that cannot see each other's counts. `RATE_AUTH=10`
   admits 20 login attempts a minute.
2. **The login lockout admits twice the failures** before locking.
3. The live-fetch dedup can schedule the same AO3 fetch twice.
4. The totals cache is computed once per worker — harmless, just wasted.

None of this is a bug today, because the live stack runs one worker. It becomes
one the moment the prod overlay is used, which is exactly what should happen for
real traffic (see §4). The two changes are coupled and should ship together.

### P1 — session tokens are stored in the clear — **FIXED 2026-08-09**

`UserSession.token` now stores a SHA-256 hex digest; the raw value never touches
the database. Plain SHA-256 rather than bcrypt is right here, unlike for
passwords: the input is 48 bytes from `secrets`, so there is no low-entropy guess
to slow down, and this runs on every authenticated request. The digest is 64
chars against a `String(80)` column, so nothing migrated.

Existing sessions are not invalidated. `get_current_user` accepts a legacy raw
row once and rewrites it to its hash in place, so active logins convert
themselves on next use and the rest expire; logout and log-out-others match both
forms, so a not-yet-converted session is still genuinely destroyed. That
fallback should be deleted once no legacy rows can remain — they cannot outlive
`SESSION_DAYS` from this deploy.

Verified end to end rather than by inspection: signup → cookie → `/api/auth/me`
returns the user, the stored column is a 64-char hex digest and not the bearer
value, and logout removes the row and leaves `me` returning `{"user": null}`.

### P1 — session tokens are stored in the clear (original finding)

`UserSession.token` holds the raw bearer value and is matched with
`filter(UserSession.token == sat)`. Anyone who obtains a copy of the database —
a backup, a dump, an errant `pg_dump` in a shared directory — can resume every
live session. Storing a SHA-256 of the token and looking sessions up by that
hash costs one line at issue and one at lookup, changes no behaviour, and
removes the class entirely. The backups directory in this repo is the reason
this is P1 rather than P2.

### P2 — chapter HTML is sanitised on every read

`api/stories.py:331` runs `sanitize_html` + `tidy_chapter_html` per chapter
request. The allowlist is sound and the CSP backs it up, so this is a
performance note, not a security one: the same work is redone on every read of
the same immutable text. With ~30k hosted works the absolute cost is modest, but
it is pure waste on the hot path of the one feature that must feel instant.

Sanitising once at write time and storing the clean HTML would remove it. The
migration needs care — existing rows hold raw HTML — so this is a background
backfill, not a switch.

### P2 — offline schema has no migration path

`lib/offline.ts` pins `DB_VERSION = 1` with a single `onupgradeneeded` that
creates the store if absent. There is no path for changing the record shape
later. The moment a saved story needs a new field — a per-chapter position, a
checksum, a saved-at-version — there is no way to migrate what is already on
readers' devices except to discard it, which is P0 again by another route.

### P2 — the API is proxied through Next.js

Every `/api/*` call goes browser → Next server (port 3000) → FastAPI (8000) via
`next.config.ts` rewrites. The reasoning in that file is sound (one port, no
CORS, no mixed content) and it should stay for now. But it does put a Node
process in the path of every search, and Node is the component with the least
headroom configured (`mem_limit: 600m`, no CPU reservation). Worth knowing about
before it is the surprise in an incident.

---

## 3. What users of comparable tools actually ask for

Research context, not defects. Ordered by how well FicAtlas is placed to act.

**~~Full-text search of story bodies is FicAtlas's differentiator.~~ Wrong — I
did not check before writing this.** AO3's search does see only titles,
summaries, notes and tags, and that gap is real. But FicAtlas does not fill it:

- `search_within` is `title ILIKE '%x%' OR summary ILIKE '%x%'`
  (`api/search.py:557`) — the same scope as AO3's search, not prose.
- the FTS index covers
  `fic_doc(title, summary, author, fandoms, characters, relationships, tags)` —
  all metadata.
- story text exists for **29,950 of 19,868,128 works (0.15%)**. The rest are
  metadata-only rows, so body search could never cover the index; over the
  ~911 MB of chapter text a tsvector index estimates at ~888 MB.

**The actual differentiator is cross-archive reach:** 19.8M works across AO3,
FanFiction.net and FictionAlley answered by one query. No single archive can do
that, and it is already built. See Plan D for what was done with it.

**Offline mode is routinely undiscoverable.** A recurring review complaint about
paid reader apps is that people cannot find how to turn offline saving on, for a
story or for a library, *after paying for it*. FicAtlas's "Save offline" lives on
the story page only. There is no way to save a search's results, a series, or a
library in bulk, and nothing anywhere prompts a reader to save before they lose
signal.

**Reading position that survives the device is expected.** Progress currently
lives in `localStorage`, so it is per-browser and dies with a cache clear. The
account system already exists; syncing progress to it is a small server-side
addition and closes a gap users notice immediately when they switch phone to
laptop.

**Repeatedly requested elsewhere, in rough order of demand:** text-to-speech;
update-checking for works in progress (notify me when this WIP updates);
customisable type, spacing and theme (FicAtlas has this, and it is good); EPUB
export (exists); bulk download; saved searches turned into feeds.

Sources are listed at the end.

---

## 4. Deployment: measured, not assumed

Measured against the live stack.

| Measurement | Value |
|---|---|
| Search latency, serial, distinct queries | median ~1.15s, worst 3.2s |
| Search latency, 10 concurrent distinct | 2.1–2.9s each, 2.9s wall |
| Backend CPU during a 30-request burst | ~28% of its 0.75-core limit |
| Postgres cache hit ratio (lifetime) | 50.4% |
| Postgres cache hit ratio (delta, 8 live searches) | **51.4%** |
| Database size / `shared_buffers` | **39 GB / 1 GB** |
| Host swap in use | 3.9 GB of 4 GB — **none of it FicAtlas**, see below |
| Host RAM free | 9.4 GB |

The cache figure is worth stating carefully, because the lifetime number from
`pg_stat_database` includes the bulk-import era and could be dismissed as a
historical artifact. It cannot: sampling `blks_hit`/`blks_read` either side of
eight fresh searches gives 51.4%, so this is what the index does *now*.

**The bottleneck is not CPU.** Neither container came close to its limit while
latency tripled under concurrency. It is I/O: a 39 GB working set against 1 GB of
shared buffers, so half of all block reads go to disk. Adding users mostly adds
contention for a starved cache. Adding workers would not have helped.

Three changes, in leverage order. All are disruptive enough to want an explicit
decision, which is why they are written down rather than done.

1. ~~**Reclaim the swap.**~~ **Withdrawn — this was a misattribution.** The
   661 MB `uvicorn` I pointed at is not FicAtlas's API. It belongs to
   `odysseus-odysseus-1`, an unrelated project on the same host, running on port
   7000. FicAtlas's own backend holds no meaningful swap: after a restart it sits
   at ~130 MB resident. The rest of the swap is `gnome-shell`, Firefox and other
   desktop processes.

   So reclaiming swap would do nothing for FicAtlas. `swapoff -a && swapon -a`
   remains a reasonable *desktop* housekeeping step — it stalls the machine for
   30–60s and needs sudo — but it belongs to a different problem, and it is not
   worth the disruption on FicAtlas's account.

   The related setting that *is* worth considering: `vm.swappiness` is 60, which
   is aggressive for a 15 GB workstation and is why an idle process gets paged
   out during any large build. Lowering it to 10 reduces that. System-wide, so
   it is your call.
2. **Give Postgres memory to work with.** `shared_buffers` 1 GB → 4 GB and the
   `db` container's `mem_limit` 4g → 8g. The compose file's own comment already
   anticipates this ("raise toward 25% of RAM"). Needs a database restart. This
   is the change that moves the cache hit ratio, and therefore the one that
   actually changes how the site feels under load.
3. ~~**Run the production overlay.**~~ **Do not do this on the current setup.**
   I recommended it before checking what it assumes. `docker-compose.prod.yml`
   sets `COOKIE_SECURE=true`, `FORCE_HTTPS=true` and `TRUST_PROXY_HEADER=true`,
   all of which are correct *behind the Cloudflare tunnel* and wrong without it.
   No `cloudflared` container is running here, and the site is served as plain
   HTTP over Tailscale. Applying the overlay as-is would:

   - break login silently — a `Secure` cookie is never sent over plain HTTP, so
     signing in appears to succeed and every subsequent request reads as signed
     out (`api/auth.py` documents exactly this);
   - re-trigger the `upgrade-insecure-requests` failure that previously stopped
     the PWA loading any JS at all (`next.config.ts` documents that one);
   - make the rate limiter trust a forgeable `CF-Connecting-IP` header, so any
     client could wear a new identity per request and bypass it entirely;
   - turn on `REQUIRE_LOGIN_TO_READ`, changing what anonymous visitors can do.

   The overlay is the right target *when the tunnel is up*. Until then, take its
   safe parts individually — which is what was applied (see §6). The one
   remaining piece is dropping `--reload`, which is genuinely a hazard on a live
   box (any stray write to the working tree restarts the API under traffic) but
   is also the documented development workflow in `CLAUDE.md`. That is a
   workflow decision, not a performance one.

---

## 5. Applied 2026-08-09 — database memory, and what it did

Changed in `docker-compose.yml` (dev stack, which is what actually serves):

| Setting | Before | Tried | **Settled on** |
|---|---|---|---|
| `shared_buffers` | 1 GB | 3 GB | **1.5 GB** |
| `effective_cache_size` | 4 GB | 6 GB | **4 GB** |
| db `mem_limit` / `cpus` | 4g / 1.0 | 5g / 3.0 | **3g / 3.0** |
| backend `cpus` | 0.75 | 2.0 | **2.0** |
| frontend build heap | unbounded (~8 GB) | — | **2 GB** |

**3 GB was too much for this host and had to be backed off.** With it in place the
machine reached 438 MB available, swap 100% full and a load average of 34, and
the desktop stuttered — which is the one outcome that was explicitly ruled out.

The mechanism is worth writing down because it makes the trade-off legible:
`shared_buffers` is *pinned* shared memory, while the OS page cache is
reclaimable. On a box short of RAM, memory given to shared_buffers is memory the
desktop can never get back — and worse, those buffers can themselves be swapped
out, turning "cached page" into "page fault to disk" and inverting the whole
point. A smaller shared_buffers leaning on the page cache is strictly more
resilient here.

Two other things were happening at once and made it worse than the setting alone:
a frontend docker build (Node sizes its heap from total system RAM and took
~8 GB) and a concurrency benchmark. The Dockerfile now caps the build at 2 GB —
it rebuilt in the same time using ~460 MB — so that particular spike cannot
recur.

Results measured **at 3 GB**, i.e. at the setting that proved unaffordable. They
are kept because they establish what the lever is worth, and they are the case
for giving FicAtlas a box that owns its RAM:

| | Before (1 GB) | At 3 GB |
|---|---|---|
| Cache hit ratio, never-run queries | 51.4% | **61.5%** |
| Cache hit ratio, repeated queries | — | 99.7% |
| 10 concurrent distinct searches, wall | 2.94s | **1.01s** |
| 10 concurrent, per-request range | 2.06–2.93s | **0.28–1.01s** |

At the 1.5 GB actually in place, expect a fraction of that gain — the shape holds,
the magnitude does not. Do not quote the 2.9x as the current state of the system.

Caveat worth keeping: the query terms differ between runs, because a term is only
"never-run" once, and rarer terms are intrinsically cheaper. The cleanest
evidence is therefore the cache hit ratio on fresh terms (51.4% → 61.5%) and the
concurrent wall time (2.9x), not the per-request headline.

The hit ratio is 61.5% rather than something near 99% because the working set is
genuinely larger than the cache: 21 GB of indexes against 3 GB of buffers. The
gain comes from holding the *hot* portion — the upper levels of the trigram
indexes — resident across queries. Pushing this further means either more RAM
than this machine can spare, or reducing the index footprint.

### Side effect, fixed

Recreating the backend container revealed that the `ficatlas-backend` image did
not contain `pytest`, even though `requirements.txt` lists it — it had been
pip-installed into the running container at some point and was lost on recreate.
The documented test command in `CLAUDE.md` broke. Rebuilding the image fixed it;
all runtime dependencies are pinned, so the rebuild was reproducible. Tests are
back to 53 passed / 14 skipped.

Worth knowing: the running container and the built image had drifted. Anything
else installed by hand into a container is not in the image either.

---

## 6. Plans

### Plan A — make offline reading trustworthy (P0) — **DONE 2026-08-09**

Implemented: `requestPersistentStorage()` called on first save (at the moment of
intent, which is when browsers are most likely to grant it); a quota check before
downloading that refuses up front with real numbers instead of failing partway;
`QuotaExceededError` given a sentence a reader can act on; `DB_VERSION` 1 → 2
with a real migration and an `onblocked` handler so an old tab cannot hang a save
forever; per-story byte sizes recorded and shown; and the Offline tab now states
plainly whether the browser will protect the saves or may clear them.

Also fixed alongside: the Offline tab's count badge read 0 until the tab was
opened, because `offlineStories` was only loaded when `tab === "offline"` while
the badge rendered its length. It now loads on mount and refreshes on focus. The
other four tab counts were already loaded on mount and were unaffected.

Original plan, kept for the reasoning:

The promise is "this will be here when the network isn't", and today it is not a
promise the code can keep.

1. Request persistent storage on first save: `navigator.storage.persist()`, and
   surface the answer. If it is refused, say so — a reader who knows their saves
   are best-effort can add the app to their home screen, which is what makes
   Chrome and Safari grant it.
2. Check `navigator.storage.estimate()` before downloading and refuse up front,
   with the actual numbers, rather than failing partway through a long work.
3. Introduce a migration path in `lib/offline.ts` before it is needed: bump to
   `DB_VERSION = 2` with a real `onupgradeneeded` that carries records forward,
   and stamp each record with the app version that wrote it.
4. Show what is saved and how much space it uses, with a way to remove
   individual works — the library's Offline tab is the natural home.

### Plan B — adopt `fetchOrFail` everywhere (P1) — **DONE 2026-08-09** (core paths)

`fetchOrFail` now takes an external `AbortSignal`, and a `fetchJson` wrapper was
added because that is what nearly every call site actually wanted. `isAbort`
distinguishes a cancelled request from a real failure so unmounts are never
reported as errors.

Converted, chosen by whether a failure changes what the reader *believes*:

- **Library `hosted` and `mine` shelves** — both used `.catch(() => {})`, so an
  unreachable backend rendered as an empty shelf. Hosted is the tab the Library
  opens on, making it the most visible place the site could say "you have
  nothing" when it meant "I could not ask". Both now render a `ShelfError` with
  the classified message and a retry where retrying could plausibly work.
- **Account `/api/auth/users`** — only the HTTP-error branch was handled; a
  network throw rejected out of the calling effect unhandled, leaving the list
  null behind its loading state with nothing on screen. That was the most likely
  failure and the only silent one.
- **Series page** — had classification but no timeout, and titled every failure
  "Not found", telling readers a series did not exist when the server had merely
  timed out.
- **Settings loaders** — converted for the timeout alone; their fallback
  behaviour was already right (local preferences remain editable when the API is
  down), but a raw fetch cannot fail, only hang.

Deliberately not converted: autocomplete/suggestion lookups, where silence is
the correct behaviour, and the admin-only discover/import operations, which
already surface their own errors. Blanket conversion would have been churn.

### Plan B — original reasoning

Mechanical, low-risk, and it retires the whole "site is unreliable" class.

1. Extend `fetchOrFail` to take an external `AbortSignal`, matching what
   `searchStories` now does, so callers can cancel superseded work.
2. Convert page by page, largest first: library (23 calls), account (6),
   settings (5), admin (2), series (1). Each conversion is a
   `fetch` → `fetchOrFail` swap plus rendering `Failure.message` instead of a
   raw exception.
3. Delete the `.catch(() => {})` handlers as they are encountered. A swallowed
   failure is how the reader ends up staring at an empty list that means "this
   is empty" and actually means "this never loaded".

### Plan C — one process's assumptions, two processes (P1)

Do this in the same change as moving to the prod overlay.

1. Decide the sharing boundary. There is no Redis in the stack and adding one for
   this alone is not obviously worth it; the honest alternatives are to keep
   `--workers 1` and raise `cpus` instead, or to move the counters into Postgres.
   Given the bottleneck is I/O rather than CPU (§4), **keeping one worker and
   giving it more CPU is the cheaper correct answer** and needs no code change.
2. If two workers are wanted later, move the rate limiter and login lockout into
   Postgres — they are low-volume writes and the database is already there.
3. Either way, correct the comments that assert "there is one API container", so
   the next person is not reasoning from a premise that has quietly changed.

### Plan D — surface the thing nobody else can do — **DONE 2026-08-09**

Rewritten after the premise above turned out to be false. The advantage is
cross-archive reach, not body search, so the work was to make that provable
rather than merely asserted.

**The results bar now shows the per-archive split.** It read "AO3 + FF.net" — a
claim that FicAtlas spans both, with nothing to back it. It now reads
`187 stories · 124 AO3 + 63 FF.net`, which is the same claim with evidence
attached: no search on either archive would have found the other 63.

`site_counts` is computed by grouping the *already-bounded* candidate subquery
(≤ ~5k rows Postgres has just materialised), so it costs nothing measurable —
narrow searches stayed at 85–336 ms. Two things had to be got right:

- it is addressed through the ORM alias `S`, not `candidates.c.site`. In the
  text-query path the candidate set is a UNION, and SQLAlchemy relabels a
  union's columns (`stories_site`), so the obvious spelling raised
  `AttributeError: site`. It was caught because the failure logs rather than
  silently returning `{}` — which would have been indistinguishable from "every
  match came from one archive".
- it is **withheld when the count is capped.** `total` caps at 5,000 but the
  candidate set behind it does not, so the first working version rendered
  "5,000+ stories · 4,975 AO3 + 425 FF.net" — a breakdown summing to 5,400 under
  a headline of 5,000. Quoting it as a percentage would have been worse, not
  better: the union pulls candidates by relevance *and* by kudos, and kudos are
  recorded far more often on AO3, so the sample is biased toward AO3 by
  construction. Exact totals only. That is also when it reads best — a narrow
  search returning 187 works across two archives demonstrates the point far
  better than "5,000+" ever could.

**`search_within` no longer overclaims.** It was labelled "Search within
results", which invites exactly the reading the premise above fell for. Now
"Filter by title or summary", with a hint saying it does not match the text of
the stories — so someone searching for a line of dialogue learns why they got
nothing, instead of concluding the story is absent.

### Plan D — original (superseded) reasoning A full-text hit with no visible
   context is much less persuasive than one that shows the sentence.

### Plan E — progress that follows the reader (P2) — **ALREADY BUILT; one real gap fixed**

Another plan written without checking. Cross-device progress sync already exists,
end to end: `api/userdata.py` exposes `/merge` with type-aware per-key merging,
`progress` is in its `ALLOWED_KEYS`, and `lib/auth.tsx` patches
`localStorage.setItem` so the reader's writes schedule a debounced sync — with a
content hash to break the merge→write→merge loop, and `sendBeacon` on unload.
Nothing needed building.

What did need fixing was a gap **this session created**. The reader now records a
position per chapter (`positions: {chapterNo: pct}`) rather than one `scrollPct`
per story, and the server merged `progress` whole-entry, last-write-wins. That
was right when the entry held a single number and wrong once it held a map:
reading chapter 7 on a phone discarded the position in chapter 3 recorded on a
laptop, so going back a chapter lost your place on the device that had never
moved.

`_merge_value` now unions the `positions` map while leaving the scalar fields —
which chapter you are on, and when — as last-write-wins, which they genuinely
are. Covered by `tests/test_userdata_merge.py` (7 tests), including the case
where a device running an older build, with no `positions` at all, syncs later
than one that has them and must not erase them.

### Plan E — original (unnecessary) plan

`ficatlas:progress` is per-browser today. The account system, session handling
and `api/userdata.py` all already exist.

1. Add a progress table keyed on (user, story), storing chapter and position.
2. Sync on chapter change and on `pagehide` — the reader already flushes there.
3. Last-write-wins on conflict, with local storage as the offline buffer. Do not
   block reading on the network: this is an enhancement to a feature that must
   keep working with no connection at all.

### Plan F — sanitise once (P2)

1. Add a `content_sanitised_at` column; sanitise on write for new imports.
2. Backfill existing rows through the worker, which already owns long jobs.
3. Read path falls back to sanitising on the fly for any row not yet backfilled,
   so the change is safe at every point during the migration.

---

## Suggested order

1. ~~§4 database memory~~ — **done**, see §5. 2.9x throughput under concurrency.
2. Plan A — offline durability. The only finding that silently destroys data.
3. Plan B — `fetchOrFail` adoption. Mechanical, retires a whole class.
4. §2 session-token hashing. One line each side.
5. Plan C — shared state. Deferred until there is a reason to run two workers;
   given the bottleneck is I/O rather than CPU, one worker with more CPU is the
   cheaper correct answer and is now in place.
6. ~~Plan D~~ — done. Plans E and F remain: progress sync, and sanitising once.

Not on this list, deliberately: the production overlay and the swap reclaim.
See §4.1 and §4.3 for why both were withdrawn.

---

## 7. Branding and aesthetics pass — 2026-08-09

**Positioning.** The brief was to lead with discovery and linking out. The About
page already did this well; the surfaces that did not were the ones people meet
first.

- Result cards. For anything not hosted — **99.85% of the index** — the primary
  button was "Details" and the archive link was a plain secondary. So the most
  prominent action on a result was a page *about* the story rather than the
  story, which reads as an archive that has mislaid its texts. The archive link
  is now primary, labelled "Read on AO3 ↗" rather than "Open on…", because the
  button should name what the reader is about to do. Same inversion on the story
  page, where "Import & read here" outranked the link to the archive — offering
  to copy a work that is perfectly readable where its author put it, and where
  their kudos, comments and subscriptions actually are.
- Metadata and manifest. "Search all fanfiction" overpromised in one direction
  and undersold in the other: it is three named archives, and naming them is both
  more accurate and more persuasive to a reader who already uses two of them.
- Landing page. Title now names the job rather than gesturing at a category, and
  a second line says where reading happens. "Always links out to the original
  archive" moved to the top of the promises list — it is not really a promise
  about conduct, it is the shape of the product.

**Aesthetics.** No browser was available in this session, so nothing was checked
visually — what follows was found by auditing the stylesheet, and the layout
still deserves a human look.

- **`--danger` was never defined.** Ten rules referenced it as
  `var(--danger, #d9534f)` — takedown cards, the settings save error, the health
  banner, the offline error state — so that hex was the real value and it never
  retuned with the theme. In light mode it sat unadjusted against a cream
  background while everything around it had been reworked. Now defined in both
  themes as an alias of the palette's existing `--red`.
- **`--warn` likewise**, falling back to `#d08a3a`. Now defined in both themes as
  amber — deliberately not an alias of `--accent`, since a caution and a brand
  highlight must not read alike.
- **The wordmark on the sign-in page was not in the brand typeface.**
  `.auth-logo` asked for `var(--font-display)`, which has never existed and
  carried no fallback, so `font-family` resolved to nothing and the browser used
  its default serif. The one element whose entire job is to look like the brand,
  on the screen people see before they have an account. Now matches `.logo`.

The only stylesheet variable still undefined is `--sidebar-w`, which is correct —
it is set from JS as an inline style and the CSS carries a `220px` fallback.

---

## Sources

User discussion and reviews consulted for §3:

- [FanFiction Plus & AO3 Reader — App Store reviews](https://apps.apple.com/us/app/fanfiction-plus-ao3-reader/id1076160335)
- [FanFiction | AO3 unofficial — ratings and reviews](https://apps.apple.com/us/app/fanfiction-ao3-unofficial/id6479576634?see-all=reviews&platform=ipad)
- [FFS — Unofficial AO3 Reader](https://apps.apple.com/us/app/id1600468025)
- [ao3downloader — feature requests and rationale](https://github.com/nianeyna/ao3downloader)
- [How to Search AO3 Like a Power User — AO3Wiki](https://ao3wiki.com/guides/ao3-search/)
- [AO3 Filtering Guide — AO3Wiki](https://ao3wiki.com/guides/ao3-filtering/)
- [Search and Browse FAQ — Archive of Our Own](https://archiveofourown.org/faq/search-and-browse?language_id=en)
- [searching & filtering ao3 — @ao3commentoftheday](https://www.tumblr.com/ao3commentoftheday/633920690637127680/searching-filtering-ao3)
- [Is AO3 overhyped or not hyped enough — Goodreads discussion](https://www.goodreads.com/topic/show/22768465-is-ao3-overhyped-or-not-hyped-enough)

Technical references for §2 (P0) and Plan A:

- [Offline data — web.dev](https://web.dev/learn/pwa/offline-data)
- [Store data on the device — Microsoft Edge docs](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/offline)
- [Offline Storage for Progressive Web Apps — Addy Osmani](https://medium.com/dev-channel/offline-storage-for-progressive-web-apps-70d52695513c)
- [Safari iOS PWA data persistence beyond 7 days — Apple Developer Forums](https://developer.apple.com/forums/thread/710157)

Note: Reddit was requested as a source but blocks Anthropic's crawler
(`400: domain not accessible`), so the community signal above comes from app
store reviews, Tumblr, Goodreads and project issue trackers instead. If you want
Reddit specifically, pasting threads in works — the search tool cannot reach them.
