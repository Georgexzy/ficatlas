# FicAtlas

A unified search engine for fanfiction. **20.0M works** indexed across AO3 (13.5M),
FanFiction.net (6.6M) and FictionAlley (30k), plus smaller curated sets and any
user-supplied EPUB. About 30,000 of those can be read in the app; the rest link
out to the archive that hosts them.

One search bar over a single index spanning multiple sites, with AO3-parity filters, a clean reader for stories hosted directly, and one-click import for fresh stories from any URL.

## What works today

### Search & discovery
- **Unified search** in one query across AO3 (13.5M works), FanFiction.net (6.6M) and
  FictionAlley (30k), plus small curated sets — Dark Lord Potter's recommended list (746 works)
  and a handful from the HP FanFiction Archive (37). Those last two are labels on stories,
  not archives of their own scale, and are listed here so the numbers are not misleading
- **Operator syntax** in any order: `fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff`
- **Tag autocomplete** — fandom, relationship, character and tag filter inputs suggest real values from the index as you type (with story counts), backed by a precomputed facets table so it's instant even on millions of rows
- **Search-first discovery** — you don't need the Import tab to get fics: when a search returns few or no indexed results and AO3 is selected, the app auto-pulls a deeper live batch (and the no-results screen has a one-click "Search AO3 directly" button). The Import tab remains the full power-user control panel for bulk scrapes
- **Canonical-tag autocomplete in Import** — the Import tab's fandom fields autocomplete index-first, then fall back to AO3's canonical fandom names (`/api/stats/suggest-canonical`) so you can discover and correctly spell new fandoms to scrape, avoiding malformed-tag errors. That vocabulary is synced into our own facets table by `ao3_canonical_fandoms.py` — 73,732 canonical names in 12 requests against AO3's public `/media/<category>/fandoms` listings, refreshed occasionally. It is deliberately **not** AO3's `/autocomplete/` endpoint, which their robots.txt disallows and which this box would otherwise have called on every keystroke
- **Per-archive result breakdown** — the results bar reads `187 stories · 124 AO3 + 63 FF.net`, which is the one thing no single archive can tell you: that a search found work in more than one place, and how much of it you would have missed searching only the archive you usually use. Counted over the same bounded candidate set as the total, so it costs nothing measurable, and **withheld when the count is capped** — the candidate set behind a capped total is not exactly that size, so the parts would not sum to the headline
- **Browse by fandom** — 5,025 fandom pages (`/fandoms`, `/fandom/<slug>`), each listing the 50 most-read works **per archive** rather than one merged list. Ranking across archives could only ever return AO3: kudos exists on 670,508 AO3 rows against 355,648 of FanFiction.net's 6.6M, on scales nine times apart, and an AO3 kudos and an FF.net favourite are different units counted by different populations. Reachable from the header and the phone tab bar, and one of the two routes a search engine has into the index — search URLs are `/?q=…`, which robots.txt blocks as an infinite crawl space
- **Browse by pairing** — 2,553 ship pages (`/ships`, `/ship/<slug>`), the other route in, and the one where a cross-archive index has something to say that no single archive can. AO3's tag pages cover AO3; `/ship/draco-malfoy-harry-potter` puts 50 AO3, 50 FanFiction.net and 50 FictionAlley works for the same pairing on one page, out of 49,962 indexed. Both orders of a pairing collapse onto one page — "Draco Malfoy/Harry Potter" (47,460 works), "Harry Potter/Draco Malfoy" (1,541) and "Harry Potter/ Draco Malfoy" (46) are one ship, not three thin duplicates. The slug is alphabetical so the URL is stable, while the heading and its search link use the spelling the archives actually use. Romantic pairings only: AO3's `/` and `&` mean different things to the people reading, and they slugify identically, so building both would merge a ship with a friendship
- **Story pages link back to both** — a work's fandom and ship hubs are rendered server-side on `/story/<id>`. Before that every link out of a story page pointed at `/?fandoms=…`, which robots.txt blocks, so the hubs fed ~750k story pages and got nothing back: the crawl went in and did not come out
- **Cross-archive popularity** — the "Most popular" sort, and the answer to the two lines above, which is why they sit together. Computed offline by `popularity_rank.py` and recomputed weekly by the worker (`REBUILD_POPULARITY`), because the score is a percentile among the works that *have* an engagement figure — so every work the crawler gives kudos to needs the percentiles rebuilt to be placed at all. The archives do not count on the same scale: average kudos/favs is 190 on AO3 against 1,676 on FanFiction.net, so a raw column sorts mostly by *which site a row came from*. Each metric is converted to a percentile **within its own archive** — "top 1% of AO3" and "top 1% of FF.net" mean the same thing whatever the scales do, and unlike a fixed multiplier it self-corrects as coverage changes. Weighted by what the action costs a reader (bookmarks/follows .35, kudos/favs .30, comments/reviews .20, hits .15), renormalised over the metrics a work actually has, shrunk toward the median by how much of the picture was visible, and blended 75/25 with the same standing per √day alive so an old work does not out-rank a better new one purely by having had longer. A `0` counts as *absent*, not as unpopular — the bulk imports wrote 0 everywhere — so unscored works get NULL and sort out of the way
- **Index freshness, stated rather than implied** — the status widget separates two things that are easy to confuse: what *we* added ("Added past 24h"), and how much of the index is a **living work its author is still writing** ("Updated past 30d", at least 125,497 works). A third figure, "Re-checked past 7d", is a claim about our own freshness — works re-read from their source archive. Shown as a floor, not a percentage: `updated_at` is NULL for 64% of the index because the bulk dumps carry no date, so the true number is higher and anything quoting it says "at least"
- **The content filter says what it is hiding** — explicit-rated works are hidden by default, and a search that returns few results now reads `1 story · 9 hidden as explicit — show` instead of silently looking like the whole answer. This was a real trap: the browse hubs rank by kudos with no rating filter, so an explicit work sits at the top of a ship page, and clicking through to its author returned one work out of ten with nothing to indicate the rest existed. Counted only when the result set fits on one page — the case where a reader would otherwise conclude that is all there is, and the only case cheap enough to count (on a 5,000-result search it costs 0.8s and tells you nothing)
- **`author:` / `by:` operator** — `author:lightning on the wave` finds that author's works. Pen names with spaces need no quotes; quoting is still accepted
- **Spelling-tolerant tag matching** — freeform tags are whatever the author typed, and 132,714 of 1,574,508 tag values (8.4%) differ from another only by case and punctuation. "fluff" exists as 44 separate values (`fluff!`, `#fluff`, `F L U F F`, `F.L.U.F.F.`); Hurt/Comfort as 33 (`hurt-comfort`, `hurt & comfort`, `hurt|comfort`). Searching any spelling now reaches the rest. Mechanical only: this merges spellings, never meanings — the semantic half genuinely cannot be automated ([FanFicFare #1340](https://github.com/JimmXinu/FanFicFare/issues/1340): Naruto alone has ~300 tags meaning the same thing)
- **Standing "never show me" list** — ships, tags, fandoms, characters and authors excluded from *every* search automatically, kept on your device and never sent in a shared search link
- **Click-to-search tags** — every fandom, ship, character, freeform tag and warning is a link that pre-fills the corresponding filter
- **"Surprise me"** — random discovery on the landing page (real stories only, no drabbles/art), optionally scoped to your active fandom filter
- **Cross-site filter correctness** — fandom is matched strictly while secondary facets (character/ship/tag) and missing metadata (word count, status, rating, language) are matched permissively, so dump rows with sparse metadata surface correctly without flooding fandom searches
- **Cross-post de-duplication** — the same fic posted on AO3, FF.net, SquidgeWorld etc. is collapsed into a single result. New imports are deduped automatically (conservative title + author matching); a one-shot batch (`dedup-crossposts`) cleans up already-indexed data. The canonical copy keeps every site's link and hosts the most recently updated full text. Cards show a "+N copies" badge; detail pages list "Also on X" links for each version
- **AO3 deep filtered scrape** — paginated tag-works listing with full filter support, run as an async job with live progress. AO3's heavy endpoints are slow (5–20s) but reachable from a normal connection; the app uses generous granular timeouts (patient read, quick connect) and retries 525s on the same host before giving up
- **Importers for five Harry Potter archives** (AO3 Open Doors / Otwarchive).
  "Available" below is what can actually be imported, which for an Open Doors
  collection is **only the works whose authors opted in** — usually a small
  fraction of what the original site held. Quoting the original site's size next
  to an import count makes complete work look like a rounding error:

  | source | imported | available | original site | status |
  |---|---:|---:|---:|---|
  | **janelleshane seed** (`janelleshane_seed`) | 111,699 | 111,953 | — | complete |
  | **HexFiles** (`hexfiles_archive`) | 831 | 834 | ~18k | complete |
  | **DLP library** (`dlp_library`) | 746 | ~1,000 | — | near complete |
  | **HPFFA** (`hpffa_archive`) | 37 | 37 | ~85k | complete |
  | **SquidgeWorld** (`squidgeworld_archive`) | 0 | — | ~30k | not scrapable, see below |

  Verify with `python3 tests/check-readme.py`.

  This table has been wrong twice, in opposite directions, which is why it now
  carries three columns. It first listed only the original sites' sizes, reading
  as though ~245,000 works had been imported when the real figure was 1,614. The
  correction then implied HPFFA was "barely started" at 37 of ~85k — but AO3's
  collection contains exactly 37 works, so 37 **is** complete. Both framings
  misled; only the available column settles it.

  **SquidgeWorld cannot be imported server-side.** It sits behind an interactive
  bot challenge that returns a JavaScript gate rather than a works listing —
  verified again: HTTP 200, zero work blurbs on the page. No amount of paging or
  patience changes that, so putting it on a timer would be pure load on a small
  volunteer-run archive in exchange for nothing. The Library page button remains
  for a human to try from a real browser.
- **AO3 Atom feeds** — per-canonical-tag feeds, polled on a 6h schedule plus on-load with auto-mirror fallback
- **Live AO3 fetch** on every search (3 pages = ~60 stories), results persisted so the index grows passively
- **FF.net discovery via Wayback Machine CDX** — FFN is Cloudflare-walled from VPS IPs, but archive.org's index isn't; we enumerate FFN URLs from Wayback and import on-demand
- **Fast paginated results** — result count capped at "5000+" for speed on large indexes; GIN indexes on the facet arrays keep fandom/tag filtering fast

### Reading & library
- **One-click "Import & Read"** — every search result for AO3/FFN with `is_hosted=false` shows an "Import & Read" button that fetches the full EPUB via FicHub and drops you into the reader
- **Result cards** — labelled dates (`Updated 3 days ago`, falling back to
  `Published`, and showing both when a work has been revised since posting),
  word count, chapters, kudos, hits, comments, bookmarks, language, cross-post
  count, and DarkLordPotter's community star rating where we have it
- **In-app reader** — Charter/Georgia serif (conventional italics), serif↔sans toggle, narrow↔wide column, A+/A−, **adjustable line spacing**, **light / sepia / dark themes**, **optional justified text with automatic hyphenation**, **estimated read time**, scroll progress bar, ← → chapter navigation. Keyboard: `←/→` chapters, `+/−` text size, `↕` line spacing, `t` cycle theme, `j` justify
- **Reader accessibility** — text size is set in `rem`, so it scales with the reader's own browser font-size setting (a `px` size ignores it, which is the setting people with low vision rely on); `lang` and `dir` are set from the story's language, so screen readers stop applying English pronunciation to the large non-English share of the index and RTL scripts render correctly; a skip link jumps past the toolbar to the prose; the progress bar is a real `progressbar` role; toggles carry `aria-pressed` and labels describing the action rather than the current value; focus stays visible and `prefers-reduced-motion` is honoured
- **Sanitised chapter HTML** — chapter bodies come from four scrapers and from user-supplied EPUBs, and the reader injects them as HTML. Everything is filtered through an allowlist on the way out (`backend/html_sanitize.py`): scripts, event handlers, `javascript:`/`data:` URLs and inline styles are dropped, outbound links get `rel="noopener"`. It doubles as a readability fix — scrapers captured page navigation and ad text as if it were prose, and unwrapping layout tags keeps the words while dropping the scaffolding
- **EPUB export / offline reading** — any hosted story has a `↓ EPUB` button that builds a valid EPUB 2 file on the fly (stdlib only, no dependencies) for reading offline in any e-reader app
- **In-app offline reading** — tap `⤓ Save offline` on any readable story to download its chapters into the browser's IndexedDB. The reader *and the story page* fall back to that saved copy when the network is unreachable (e.g. away from your Tailscale connection), an **Offline** tab in the library lists everything saved on the device with its size, and a service worker caches the app shell so it boots with no connection. Pre-save on wifi, read anywhere
- **Offline saves that survive** — browsers evict origin storage under disk pressure, oldest origin first. So "saved" is, by default, a promise the browser is free to break silently, which is the most-reported failure of comparable reader apps. FicAtlas requests persistent storage **when an installed app starts**, not only when you save: [WebKit grants it on heuristics that include "whether the website is opened as a Home Screen Web App"](https://webkit.org/blog/14403/updates-to-storage-policy/), so asking at launch is what gets it granted on iOS — and the same policy gives an installed app the same quota as a browser, up to 60% of disk, not the 50MB figure that circulates. The quota is checked *before* downloading so a long work fails up front with real numbers rather than part-way through, every save is **read back** before it reports success, and the Library audits saves on load and says plainly which the browser has emptied — while you still have a connection to fix it
- **Downloads that survive a tunnel** — a save cut short by a lost connection keeps every chapter it fetched and records the rest, so the work is readable up to that point and finishes itself when the connection returns. It says so too: `◐ Saved, 22 to go` rather than a flat "saved" on a work missing sixty chapters. Long works also wait out the site's own rate limit instead of failing — a 199-chapter work used to abort at chapter 166 with HTTP 429 and discard everything
- **Your offline shelf follows you** — the *list* of works you chose to keep offline syncs across devices; the chapters deliberately do not, since they are megabytes and the other device can fetch the text itself. Open the Library on your phone and the works you picked on your laptop are listed, one tap from downloading
- **Follow a work** — the one subscription list AO3, FanFiction.net and FictionAlley cannot give you between them. There is no notification queue: an update is a *comparison* against what you had seen, answered at read time, so a work is flagged correctly whichever path updated it and no missed event can leave a follow permanently stale
- **Similar stories** — every story detail page shows an "If you like this, try…" section, recommending reads by shared fandoms/ships/tags with overlap scoring (ships weighted highest, then fandom, then freeform tags, with a small popularity tiebreaker)
- **Scroll-position reading progress** — debounced save of chapter + scroll position; opening a chapter you've partly read jumps back to where you left off
- **iOS Books-style hosted library** — book covers with hashed gradients, hover lift, drop shadow. Each shows an amber progress bar across the bottom and `Ch N/M · X%` when you've started reading. Clicking deep-links to your saved chapter, not chapter 1
- **Continue Reading** — story detail page replaces "Read Chapter 1" with "Continue Chapter N · Start over" when progress exists
- **EPUB upload (single or bulk)** — drag/drop up to 100 .epub files; mobile-friendly file picker
- **Bulk URL import** — paste a list of AO3/FFN links (one per line) and import them all sequentially with a live progress bar and per-URL success/fail results
- **DLP star ratings** — DarkLordPotter runs a community rating on every library
  thread; those are collected and shown as stars with the value beside them,
  filterable by minimum (`dlp_min_rating`, or the sidebar's 3+/3.5+/4+/4.5+).
  DLP's list is already curated, so the rating separates the best of it from the
  merely-included
- **DLP badge & cross-post links** — DLP-curated stories show a purple "DLP" badge; cross-posted works show a "+N copies" badge in results and "Also on AO3 / FF.net / SquidgeWorld" links on the detail page
- **Authors can verify their account and say what they permit** — an AO3 or
  FanFiction.net writer can prove they control their account and record a
  standing choice: host the full text, list it but never store the writing, or
  do not index it at all. It applies to their whole back catalogue **and
  everything they post later**, so it is a once-only thing.

  Proof is control of the account, not a login. Neither archive has an API or
  OAuth, and AO3 has publicly told its users never to give a third-party app
  their password — so a one-time token goes in the author's own profile and is
  read back from the public page. AO3's `robots.txt` disallows `/works?`,
  `/autocomplete/`, `/downloads/` and the search endpoints; `/users/` is not
  disallowed, which is the path this uses.

  **No email is sent, and nothing claims otherwise.** There is no SMTP configured
  and no domain, so the takedown form does not promise a reply — it says the
  removal has already taken effect, which is true and is the thing the author
  wanted. The address it collects is only for the case where someone needs to ask
  a question. If mail is ever wanted, `SMTP_HOST`/`SMTP_FROM` and the confirmation
  copy are the pieces to reconnect, and the flow would need code to actually send.

  **Removal never requires any of this.** The two are deliberately asymmetric:
  getting a removal wrong hides a work that need not have been hidden, which is
  recoverable; getting a permission wrong means hosting someone's writing
  without consent, which is not recoverable by the person it happens to. So
  proof is required only for saying *yes*, and revoking needs no proof either —
  it can only ever reduce what the site may do.

  **AO3 only, and that is a real limitation rather than a preference.** Every
  request to FanFiction.net from this server — profile, home page, any
  User-Agent — returns 403 behind Cloudflare's "Just a moment…" interstitial, so
  their profile cannot be read to check a code. (The rest of the project already
  works around this: `ffnet_enrich.py` reads FF.net metadata out of the Wayback
  Machine, which is no help here, because a code pasted today is not in an
  archived snapshot.) Independently, FF.net profiles are keyed by numeric id
  while stories carry a pen name, and all 6.5M FF.net rows have `author_url`
  NULL — so there is nothing stored to join a verified profile to its works.

  FF.net authors are **not** shut out, though: the restrictive policies need no
  verification at all, because proof exists to stop someone licensing writing
  that is not theirs, and has nothing to protect against when a statement only
  ever *removes* permission. So they can say "never store my text" or "don't
  index me", just not "host my work"

- **Authors can see and manage everything held under their name** — `/permissions`
  lists every indexed work, whether its text is stored here, and lets them take
  down individual works or set a standing restriction, in one flow: who are you,
  here is what we hold, decide, and prove it only if you are granting rather than
  withdrawing. Usable **without** verifying, for the same reason as everything
  else here: it shows nothing that is not already on that author's own results
  page, and requiring proof before someone may look at what a site holds about
  them would be the wrong way round. Reachable from the footer, from About, and
  from the foot of every story page. Verified authors are marked on result cards
  with a quiet "✓ author verified" — deliberately not a badge, since it states a
  fact about consent rather than quality and must not read as a ranking

  It does **not** read consent out of prose. Fandom's "blanket statement"
  convention lives in the very profile field this reads, but those statements
  are written about transformative works (podfic, translation, remix), "archive"
  in them commonly means a personal copy, and there is no standard format.
  Treating one as permission to rehost would be the mistake the opt-out detector
  exists to avoid, pointed the other way. A verified statement does outrank the
  opt-out heuristic, which is the only way a false positive there can be
  corrected — by the one person entitled to correct it
- **Author opt-outs are honoured** — some authors state in a work's summary that
  they do not want it reposted or redistributed on other sites. FicAtlas treats
  that as an explicit no-index notice: such works are skipped at ingest and
  removed from the index (metadata entry *and* hosted full text, which cascades
  with the row). Detection is deliberately conservative (`backend/external_optout.py`)
  so an ordinary summary is never misread as a refusal. `backend/optout_sweep.py`
  is the one-shot cleanup that finds and removes already-indexed matches
  (dry-run by default; `--apply` deletes)

### Accounts & sync
- **Who may register** is `SIGNUP_MODE` in `.env`: `open` (anyone), `invite`
  (one shared code in `SIGNUP_CODE` — not per-person invitations, no expiry, no
  revocation) or `closed`. The signup form asks `/api/auth/signup-policy` and
  renders a code box or hides the tab accordingly. Note that **searching needs
  no account at all**, so this gates bookmarks, follows and sync rather than
  access to the index. The first account created becomes `owner`
- **Following a work** — follow any unfinished story from a result card or its
  own page, and `/follows` lists everything you follow with new-chapter counts,
  updates first. The unread count sits on the avatar in the header
- **Optional accounts** — username + password (bcrypt), no email required. 90-day httponly cookie sessions.
  Session tokens are stored as a SHA-256 digest, not verbatim: the table would
  otherwise be a list of working credentials, and a backup or a stray `pg_dump`
  would hand over every live session
- **One operator page** — `/admin` carries index health and the takedown queue as
  tabs, reachable from the user menu for an account that can manage. They were
  two routes reached only from Settings, each re-implementing the same shell and
  the same "not an operator" gate; two copies of an access check is one too many,
  since the one that drifts laxer is the bug
- **Cross-device sync with merge** — bookmarks, reading progress, recents and settings sync to your account and **merge** across devices rather than overwriting: bookmarks union, progress keeps the most recently updated per story, recents union (capped). Using your phone and laptop together never silently drops data
- **Resilient sync engine** — dirty-key retry queue, request coalescing, re-sync on tab focus / network reconnect / 60s interval, and a `sendBeacon` flush on page unload
- **Account management** (`/account`) — active sessions list (see every signed-in device), change password (signs out other devices), sign out all other devices, delete account (password-confirmed, cascades)
- **Security** — login rate limiting (8 fails → 5-minute lock), opportunistic expired-session cleanup
- **Works signed-out too** — bookmarks, recents and progress still work locally in localStorage with no account

### Data seeds
- **HuggingFace FFN metadata dump** — 6.6M FFnet rows (IDs 1–10.9M, 2014-era). The single biggest free seed. Auto-downloads via `huggingface_hub` from inside the backend container. Uses Postgres `ON CONFLICT DO NOTHING` for idempotent batched inserts. It carries **no completion status and no dates**, so rows import as `status=unknown` rather than being asserted unfinished — a `complete` search treats unknown permissively, so they stay findable
- **Background enrichment worker** — a separate `worker` container owns all recurring work, so heavy backfills never compete with request handling and never die with an API restart. Each loop is behind its own env flag:

  | loop | every | per run |
  |---|---|---|
  | AO3 title repair + work-page harvest | 1 min | 300 works |
  | AO3 listing harvest (20 works/request) | 3 min | 5 pages of one fandom |
  | Alt archives (HPFFA/HexFiles) + DLP | 12 min | 4 pages; 25 imports; 12 ratings |
  | Recent works (tracked fandoms) | 20 min | 3 pages |
  | Wayback metadata harvest (costs AO3 nothing) | 15 s fetch / 90 s discovery | 20 works |
  | Source-deletion check (auto-withdraw) | 90 min | 40 works |
  | FF.net enrichment via Wayback | 30 min | 200 stories |
  | FF.net enrichment (Wayback → FicHub) | 30 min | 200 works |
  | Stale refresh (update checks) | 30 min | 40 works |
  | Cross-post dedup | 3 h | — |
  | AO3 atom feeds | 6 h | — |

- **Update tracking** — the index is not a snapshot of import day. Tag pages sorted by `revised_at` are AO3's own update ordering, so tracked fandoms surface changes on their own; any re-encounter applies updates forward-only; and a stale-refresh loop re-reads works most likely to have changed. That last one is weighted rather than naive: `exp(-days_since_update/365) × ln(1+kudos+hits) × ln(2+days_since_checked)`, so a fic updated last week is checked far sooner than one dormant three years, however popular. A measured pass found 22 of 26 re-read works had gained chapters
- **Live AO3** filling the gap from 2021 onward
- **FictionAlley** for offline HP archive with full text
- **FicHub** for any fresh per-URL fetch

### Series detection
Neither AO3's series field nor a shared title word reaches most of what readers
call a series, and FanFiction.net has no series feature at all — its authors
write the relationship into the summary by hand. **119,671 series** are currently
detected across 267,906 works. Four detectors, each reading a different signal,
and each refusing more than it accepts:

- **Shared titles** (`series_detect.py`) — the original: "Dangerverse Book 1" and its siblings. Useless where titles have nothing in common
- **Declared position** (`series_from_summary.py`) — "part 7 of Sacrifices", "Book Two of the X". A positional declaration is what NAMES a series and proves the author thinks in sequence; a bare mention ("set in my X universe") is far too loose to group on alone and is only used to extend a series already established
- **Sequel chains** (`series_from_sequels.py`) — 103,302 works say "Sequel to X" and ~10,000 say "Prequel to X". That is a *directed* statement, so chaining the edges orders the sequence. 73% of declarations resolve against the author's own catalogue, and this is now the largest source: **70,595 series covering 146,843 works**. Branches (two sequels to one story) and cycles are dropped rather than resolved by guesswork — a fork is not a reading order
- **Ordering** (`series_ordering.py`) — an explicit position wins; then a canon anchor ("AU of GoF" means the fourth book, so it sits fourth); then publication date, which only ever decides where an unplaced work falls *between* anchored ones. Date never orders anything alone: authors repost, backdate, and publish prequels years later. Each member records which signal placed it

The worked example is Lightning on the Wave's Sacrifices Arc — seven works whose
titles share not one word, one stated position, and the rest of the order in
phrases like "AU of CoS". Book three was missing from the index entirely and was
recovered through the Wayback route above:

```
1 Saving Connor                        [date]      5 Freedom And Not Peace     [canon: GoF]
2 No Mouth But Some Serpent's          [canon:CoS] 6 Wind That Shakes the Seas [canon: OoTP]
3 Comes Out of Darkness Morn           [canon:PoA] 7 A Song In Time of Revolution [canon: HBP]
4 Maze of Light                        [date]      8 I Am Also Thy Brother    [declared: part 7]
```

### Settings & UX
- **Your data** — every group the site keeps on your device (recent searches, reading progress, bookmarks, mute list, preferences) with its size, a Clear button per group, and a JSON export, so "it never leaves your device" is something you can check rather than take on trust
- **Settings page** at `/settings` — tracked fandom, poll-on-load, live AO3 fetch, default sites/sort/per-page, feed filters, reader font and width, explicit visibility, and an advanced **direct-crawl toggle** (off by default). Persisted server-side
- **Direct crawl toggle** — opt-in scheduled crawling of AO3/FF.net. AO3 works from a normal home/residential connection (its heavy filtered-works and feed pages are just slow, ~5–20s, which the app now waits out with generous granular timeouts and same-host 525 retries). FF.net stays Cloudflare-blocked for direct server requests regardless of IP — use one-click FicHub URL import for it. Controlled at runtime from the DB setting (no restart); `GET /api/library/crawl-status` reports recent crawl outcomes
- **Index status widget** — per-site counts, total stories, total words, DLP and HPFFA counts
- **Fully responsive** — proper mobile viewport; on phones the filter sidebar becomes a slide-out drawer with a backdrop, an active-filter badge, and an Apply button (instead of being hidden). Tablet/phone/small-phone breakpoints, 40–44px touch targets, horizontally scrolling library tabs, 2-column book grid, full-width stacked actions. Works from any host over Tailscale/LAN
- **Loading skeletons** while results load, smooth scroll-to-top on page change
- **Keyboard shortcuts** — `/` focus search, `Esc` close help, `← →` navigate chapters, `+ −` resize reader, `↕` line spacing, `t` reader theme, `j` justify text. Reader shortcuts are suppressed inside any editable field, so typing never pages the chapter

## Stack

- **Backend** — FastAPI · SQLAlchemy · PostgreSQL 16 · APScheduler · httpx · BeautifulSoup4 · pyarrow · huggingface-hub
- **Frontend** — Next.js 15 (App Router, `/api/*` rewrite proxy to backend) · TypeScript · Tailwind base + custom editorial CSS
- **External services** — FicHub (cross-archive download API), Wayback Machine CDX, HuggingFace Hub
- **Data sources** — HuggingFace `mrzjy/fanfiction_meta` (6.6M FFN rows) · AO3 Atom feeds · AO3 tag-works deep-scrape · DLP library list · Wayback Machine FFN URL discovery · FicHub per-URL · FictionAlley dump · uploaded EPUBs

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

The backend (8000) and Postgres (5432) are bound to `127.0.0.1` in `docker-compose.yml`, so they are reachable from the host but not from the LAN or the tailnet. This is deliberate: Postgres uses a default password in development, so neither port should be exposed to a network. The app is unaffected — the frontend reaches the backend over the internal compose network.

`/api/library/*` mutations are no longer unauthenticated — every destructive endpoint requires `admin`, and roles are enforced server-side (see **Accounts & sync**). That is a change from an earlier version of this file, which described them as open.

**Putting this on the internet is a different configuration**, and it is now its
own compose project rather than an overlay. `docker-compose.public.yml` runs the
public tier — [ficatlas.com](https://ficatlas.com) — as `ficatlas-public`,
alongside the dev stack and **sharing its database**, so indexing done by the dev
worker is live on the public site immediately.

```
visitor → Cloudflare (TLS) → cloudflared → nginx :8080 → web-{blue,green} :3000
                                             nginx :8081 ← (Next rewrites /api/*)
                                                          → api-{blue,green} :8000
```

Deploys are blue/green and go through one script, which is the only supported
way to ship:

```bash
deploy/promote.sh              # build, verify, switch
deploy/promote.sh --status     # what is live now
deploy/promote.sh --rollback   # back to the other colour
```

It builds images tagged by commit SHA, starts the idle colour, waits for it to
answer, repoints nginx and reloads, then verifies *through* nginx before keeping
the old colour for 120s so a rollback is a reload rather than a rebuild.

See **[deploy/README.md](deploy/README.md)** for the Cloudflare settings, the
cache rule, and why the origin speaks plain HTTP behind a TLS tunnel.
**[DEPLOYMENT.md](DEPLOYMENT.md)** covers the older single-host overlay
(`docker-compose.prod.yml`), which still works for a simpler setup.

Three things about the frontend image are build arguments rather than runtime
variables, and each one failed silently in production before it was:
`FORCE_HTTPS`, `INTERNAL_API_URL` and `NEXT_PUBLIC_SITE_URL`. Next resolves
`headers()` and `rewrites()` during `next build` into the route manifest, and
substitutes `NEXT_PUBLIC_*` textually, so setting any of them in the container
does nothing. `promote.sh` passes all three.

Two things about that overlay worth knowing, because both were wrong in an earlier version and both fail silently:

- **Tailscale keeps working**, but only because the frontend binds the tailnet address as well as loopback. Set `TAILSCALE_IP` in `.env` (`tailscale ip -4`). Loopback alone means your phone connects to `100.x:3000` and finds nothing listening. It is the *same app and same database* over the tailnet — not a separate admin panel — so roles are identical and admin actions there change the public site.
- **`upgrade-insecure-requests` is opt-in** (`FORCE_HTTPS=true`, set only in the overlay). Sending it without real TLS makes the browser rewrite every asset URL to `https://`, and over plain-HTTP Tailscale every script fails with `ERR_SSL_PROTOCOL_ERROR` — the page shell loads and nothing else does.

### Hardening scripts

Two one-off scripts, both reversible, both to be **run as files** — do not paste their contents into a terminal, because they call `exit` and that closes your shell:

```bash
sudo bash harden-network.sh        # stop containers reaching your LAN and this host
sudo bash setup-windows-share.sh   # SMB backup target: credentials out of fstab, correct SMB version
```

`harden-network.sh` is the significant one. By default a container can reach your router's admin page and this machine's SSH port, so any flaw that lets an attacker make requests from inside a container reaches the home network. The tunnel does not help — it protects inbound, this is outbound.

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

### FictionAlley (≈30k Harry Potter stories with full text)

```bash
# Copy the FictionAlley pg_dump folder into the db container:
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

### AO3 metadata dump (~13M works) — the richest source

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

### Enrichment: filling what the dumps left empty

The bulk dumps are wide but shallow. The AO3 metadata dump carries no summaries
at all, no update dates, and truncates long titles mid-phrase; the FFN dump
carries no characters, ships, dates or engagement counts. There is **no bulk
source that fixes this** — see Known limitations — so it is recovered from the
sites themselves, in the background, at a deliberately modest rate.

- **`ffnet-harvester/`** — FanFiction.net engagement (Reviews, Favs, Follows),
  25 works per request from a listing page. It is its own container on the
  stock Playwright image, and that is not a preference: FF.net answers **403 to
  every HTTP client** and 200 to a real browser engine. Measured on the same URL
  in the same minute — httpx with a Chrome User-Agent gets the interstitial,
  headless Chromium gets 25 stories with the counts inline. So every httpx path
  (`ffnet_enrich`, `fichub_meta`, the live fetchers) is locked out by
  construction. The refusal is per *session* rather than per rate — after ~3
  pages one browser context is refused indefinitely, while the same pages on a
  fresh context return immediately — so each page load gets its own context.
  Sorted by favourites, because "recently updated" walks works the 2017-era dump
  never contained. Measured: 250 works per 10 pages, ~432,000/day at a 6s pace.
  FF.net publishes no view counter anywhere, so `hits` stays empty for it.

- **`ao3_listing_harvest.py`** — the bulk route. A tag-works listing carries
  full metadata for **twenty works per request** (measured: 19/20 with a
  summary, 20/20 with word count and updated date), so the same coverage costs
  ~650,000 requests instead of 13,000,000 — and 20x less load on AO3 for
  identical data, which makes it the polite option as well as the fast one.
  Deep pagination is not capped; page 5000 of a large fandom still returns 20
  works. Walks the canonical fandoms largest-first, so it can be stopped at any
  point with most of the value already banked.

- **`wayback_harvest.py`** — the one route that costs AO3 nothing. archive.org
  has snapshotted AO3 work pages for years, and a snapshot is a complete copy:
  summary, tags, dates, stats. Measured against the CDX index on a 57,158-work
  sample, **81.2%** of the AO3 works Wayback holds are ones we already have but
  cannot summarise — the coverage is weighted toward exactly the popular works
  the gap is made of — and another 18.2% are works we do not hold at all. This
  is also the collection route the OTW names as acceptable, alongside search
  indexing. Two loops: a CDX walk that discovers ~9,000 work IDs per request
  into a queue, and a fetcher that drains it. Paced by its own budget, not the
  AO3 one — throttling it against AO3 would discard the entire point.

- **`ao3_title_repair.py`** — walks AO3 works whose dump title was truncated
  (identifiable because no real title ends on a dangling "and/of/the/with") and
  re-reads the work page. Since the page is being fetched anyway, it harvests
  everything on it: summary, published and updated dates, word count, chapter
  counts, language, kudos, hits, comments and bookmarks. Gap-filling only — the
  dumps stay authoritative for anything they supplied, except the title, which
  may be *extended*, and the engagement counters, which only rise.

  The queue is ordered most-read-first so the fics people actually open are
  fixed before the long tail, with the day a row was last checked as the primary
  key so unreachable works cannot camp at the head of the queue forever.

- **`ffnet_enrich.py`** — recovers FF.net genres, characters, ships, dates,
  completion status and favourite counts from archive.org snapshots, since FFN
  itself is Cloudflare-walled.

- **`ao3_canonical_fandoms.py`** — syncs AO3's canonical fandom vocabulary for
  Import autocomplete (see above).

Rate limiting is adaptive. AO3's robots.txt sets no `Crawl-delay` for `*`, but
AO3 enforces a limit it does not publish: 8 connections at ~0.9 req/s drew 76
HTTP 429s in a single 300-work pass. The limiter widens multiplicatively on a
429 (honouring `Retry-After` across the whole pool) and recovers slowly on
sustained success, converging just under the real limit without anyone having
to know what it is.

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

Public imports and live-fetch indexing refuse works whose author explicitly
opt out of external reposting (a 403 for a pasted URL, an ingest skip otherwise);
private imports to a signed-in reader's own library are unaffected, since those
republish nothing. See the opt-out note under **Reading & library**.

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
| `site:ao3` | Restrict to one site — also `ffnet`, `fictionalley` |
| `site:fanfiction.net` | The archive names are aliased: `ff.net`, `ffn`, `archiveofourown.org`, `ficalley` and the domain you pasted all resolve |
| `-tag:fluff` | Exclude (prefix any operator with `-`) |
| `complete` `wip` `mature` | Standalone status/rating words |
| `https://archiveofourown.org/works/12345` | Paste a URL to import the story |

Free text runs through Postgres `websearch_to_tsquery`, so `"exact phrase"`, `or`,
and `-word` work in the non-operator part of a query too.

**Operators with a fixed set of values take exactly one word**, so a filter and a
query combine without quoting: `rating:M harry potter` filters by rating *and*
searches for "harry potter". That applies to `site:`, `rating:`, `status:`,
`updated:`, `words:`, `crossover:` and `series:`. Operators whose values are
genuinely multi-word — `fandom:`, `ship:`, `char:`, `tag:`, `author:`, `lang:` —
run to the next operator, stopping before trailing shorthand, so
`fandom: Harry Potter complete >100k` still yields all three. Quote a value to
bound it explicitly.

An archive name that is not recognised drops the filter rather than applying one
nothing can match: `site:goodreads harry potter` searches every archive for
"harry potter" instead of returning zero results that read as "the index has
none of this".

### How filters treat missing metadata

Filters are **strict**: a story matches only if it actually carries the value. This
matters because the bulk sources are uneven — the FFN dump has fandom, rating and
word count but no characters or ships. Previously a story with no data for a field
matched *any* filter on it, so filtering by a ship returned millions of stories
with no ship at all.

Tick **"Include stories with missing info"** in the sidebar (or pass
`include_unknown=true`) to widen a search back to rows whose metadata was never
captured.

#### Completion status is the uneven one

Most sparse fields are uneven by *degree*. Completion is uneven by *value*, which
is worse, because it makes one half of the filter look like it works while the
other silently does not:

| site | complete | in progress | not stated |
|---|---:|---:|---:|
| AO3 | 7,568,883 | 5,638,120 | 74,386 |
| FanFiction.net | 1,293,899 | **0** | 5,278,073 |
| FictionAlley | 21,453 | **0** | 8,496 |

"Complete" genuinely works across all three archives. "In Progress" is AO3-only —
not because the other archives have no unfinished works (FanFiction.net is full of
them) but because the bulk dump has no completion column at all, so those rows are
honestly recorded as *not stated* rather than guessed at. The sidebar says so when
you select it, rather than quietly handing back an AO3-only result set.

This is narrowing rather than permanent. FF.net prints "Status: Complete" on a
finished work and nothing at all on an unfinished one, so on a page we have
actually fetched, the absence of that marker is evidence — and `ffnet_enrich.py`
now records it, where it used to keep only the positive case and leave the row
*not stated* forever. Enrichment is Wayback-paced, so the gap closes slowly.

There is no **Abandoned** filter. The status exists in the schema but nothing has
ever written it — it matched 0 rows out of 19.8M — and a filter guaranteed to
empty your results is worse than a missing one. It comes back when something
populates it.

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
┌──────────────▼──────────────────────┐      ┌────────────────────────────┐
│  FastAPI backend (port 8000)        │      │  worker (same image)       │
│  search · stories · hubs · library  │      │  scheduler · AO3 harvest   │
│  stats                              │      │  FFN enrich · dedup        │
│                                     │      │  hubs · popularity         │
└──────────────┬──────────────────────┘      └─────────────┬──────────────┘
               │                                           │
┌──────────────▼───────────────────────────────────────────▼──────────────┐
│  PostgreSQL 16                                                          │
│  stories · chapters · crawl_jobs · facets · users                       │
│  fandom_hubs · ship_hubs                                                │
└─────────────────────────────────────────────────────────────────────────┘
```

Both hub tables have the same shape and are written by one builder
(`hub_build.build_groups`); `fandom_hubs.py` and `ship_hubs.py` differ only in
how they collapse facet rows into groups — an author suffix for fandoms, pairing
order for ships.

The worker runs the scheduler (`RUN_SCHEDULER`) so the API never double-polls,
and owns every long backfill. Anything started with `docker compose exec backend`
instead would die on the next API restart.

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
- `GET  /api/hubs` · `GET /api/hubs/{slug}` — fandom hubs, backing `/fandoms` and `/fandom/<slug>`
- `GET  /api/ships` · `GET /api/ships/{slug}` — ship hubs, backing `/ships` and `/ship/<slug>`
- `GET  /api/stats/sites` · `GET /api/stats/totals` — index counts, totals, and freshness (`updated_last_month` / `_quarter` / `_year`, `checked_last_week`)
- `GET  /api/stats/suggest?kind=&q=` — tag autocomplete · `POST /api/stats/refresh-facets` — rebuild autocomplete index
- `POST /api/auth/signup` · `/login` · `/logout` · `GET /api/auth/me` — authentication
- `POST /api/auth/change-password` · `/logout-all` · `/delete-account` · `GET /api/auth/sessions` — account management
- `GET /api/userdata` · `PUT/DELETE /api/userdata/{key}` · `POST /api/userdata/merge` — per-account synced storage

## Known limitations

- **Offline reading requires HTTPS, and this is easy to miss.** Service workers
  and the Cache API only exist in a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts):
  `https`, or `localhost`, and nothing else. Reached over plain http at a LAN or
  tailnet IP — the usual way to open a self-hosted instance from a phone —
  `navigator.serviceWorker` does not fail, it is **undefined**, so nothing is
  ever cached. Measured on this deployment: `http://localhost:3000` reports
  `isSecureContext true`, `http://192.168.1.250:3000` reports `false`.
  Everything behaves normally right up to the moment it matters — pages load,
  stories save (IndexedDB *is* available on insecure origins), and reading works
  while the app stays open — and then a cold launch with no connection shows
  nothing at all. Tailscale solves it without a domain or public exposure:
  `sudo tailscale serve --bg --https=443 http://127.0.0.1:3000` gives a real
  certificate for `<machine>.<tailnet>.ts.net`. **Re-add the app to the home
  screen from the https origin afterwards** — an installed PWA is bound to the
  origin it was installed from, and the old icon keeps pointing at the http one.
  The Library says so explicitly when opened on an insecure origin.

- **No bulk DATASET for AO3 summaries** — though this is no longer the ceiling it
  once looked like. All 13M AO3 rows arrived without a summary, and no published
  dataset fixes that (see below). What does fix it is reading AO3's own listing
  pages, twenty works at a time, which is ~15 days of polite crawling rather than
  the ~300 days a one-work-per-request harvest implied. The gap was never really
  about volume; it was about request shape. Checked directly rather than from
  descriptions: the four `archiveofourown-meta` mirrors on HuggingFace are the
  same 7.36 GB `combined_metadata.jsonl` re-uploaded, whose records carry twelve
  metadata keys and no summary field;
  `nyuuzyou/archiveofourown` (the only full-text set) has been **permanently
  disabled**; the Webis Trigger Warning Corpus *has* a `summary` field but ships
  "dehydrated" with it blank in every sampled record; the Kaggle dumps are 45–95 MB.
  AO3 publishes no bulk API. The same dump also truncates titles at source —
  verified by locating a known-bad work inside the file — so the work-page
  harvest is not a fallback, it is the only route. At ~0.3 req/s it covers the
  ~396k identifiable rows rather than all 13M.
- **AO3 rate-limits harder than its robots.txt implies.** No `Crawl-delay` is
  published for `*`, but sustained ~0.9 req/s draws HTTP 429s. Two endpoints are
  disallowed outright and are no longer used by anything automated:
  `/works/search?` (the free-text live-fetch path — fandom-scoped searches use
  the permitted `/tags/<tag>/works` instead) and `/autocomplete/`.
- **FanFiction.net cannot be crawled at all, and the Internet Archive is the way round it.** FF.net has blocked automated access since 2021 and does it with Cloudflare. Eight endpoints were tested from this host — story pages, listings, author profiles, the Atom and RSS feeds, and both mobile URLs — and every one returns the same "Just a moment" challenge, so this is not a datacenter-IP problem. FicHub (which solves the challenge on its end) still works per URL but rate-limits hard and has itself reported FF.net as "fragile". The documented community workarounds are a human loading pages in their own browser, or Cloudflare-evasion proxies such as FlareSolverr and `undetected-chromedriver`; the second is not something this project will use.
  What does work is not asking FF.net. The Archive crawls it independently, their CDX API is public, and `web.archive.org` is not behind the challenge — 20,000+ successful FF.net story captures since January 2026, which was the query limit rather than the ceiling. `ffnet_wayback.py` reads those snapshots for metadata and text, the same route `wayback_harvest.py` already takes for AO3 and on the same footing: the OTW's own scraping statement names backing works up to the Wayback Machine as acceptable use. It is **not** parity with AO3 — coverage is partial, it lags by however long a recrawl takes, and most captures are redirects or non-first chapters rather than usable pages. Measured against the Archive's index, 2,385 of 2,464 sampled FF.net story ids are already here, so the bulk dump is ~96% complete and this closes the remainder.
- **AO3 latency, not blocking** — from a normal residential connection AO3 is reachable, but its filtered-works and atom-feed endpoints are slow to generate (≈7s typical, spiking to 15–20s under load) and intermittently return Cloudflare 525s when their origin is overloaded. The app handles this with generous granular timeouts, same-host 525 retries with backoff, and a brief self-cooldown only after many consecutive failures (not a single slow response). On a datacenter IP AO3 may block outright (525/timeouts on everything) — a Tailscale exit node or WARP routes around that. The HuggingFace dump, FicHub per-URL import, DLP, and FictionAlley remain the fastest bulk paths.

## Acknowledgements

- **AO3** — for publishing the official data dump
- **FicHub** — for the cross-archive download API that bypasses Cloudflare cleanly
- **Internet Archive** — for preserving FanFiction.net
- The unofficial **FictionAlley archive maintainers** — for keeping the dead site alive in pg_dump form
- **Webis / Zenodo** — for publishing fanfiction research corpora openly, even where they were not the right fit here

## Status

Personal project, not a finished product. Things listed under "What works today" do work; everything else is aspirational.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE) — read it, run it, change it, share your
changes, for any non-commercial purpose. Not for sale, paid hosting, or
ad-supported services.

The restriction is deliberate and it is not about the code. FicAtlas indexes —
and for roughly 30,000 works, stores — writing that fan authors published for
free on archives that promised not to profit from it. A permissive licence would
let anyone put that work behind adverts without asking them.

The licence covers the software only. The fanfiction belongs to whoever wrote
it, is not licensed here, and has its own takedown route on the site.
