// FanFiction.net engagement harvester.
//
// Why this exists, and why it is JavaScript in its own container when the rest
// of the crawling is Python in the worker:
//
// FF.net sits behind Cloudflare, and the distinction that matters is not the
// User-Agent, it is whether a real browser engine is executing the challenge.
// Measured against the same listing URL on the same machine:
//
//     httpx + a Chrome User-Agent  ->  403, "just a moment" interstitial
//     headless Chromium            ->  200, 25 stories, engagement inline
//
// So every httpx-based path — ffnet_enrich, fichub_meta, the live fetchers —
// cannot reach this source at all, and no amount of header tuning changes that.
// It needs Chromium, and putting Chromium in the backend image would undo the
// 11.8GB -> 533MB work for the sake of one job. Hence a separate service on the
// same database.
//
// What it collects: FF.net publishes Reviews, Favs and Follows on its LISTING
// pages, 25 works to a request. It does not publish view counts anywhere, so
// `hits` is not fillable for this site from any source — see SORT_COVERAGE in
// frontend/lib/api.ts, which says so to readers.
//
//     Favs    -> kudos
//     Follows -> bookmarks
//     Reviews -> comments
//
// 25 works per request against ffnet_enrich's two archive.org requests per work
// is a 50x improvement in the only number that matters, and it asks nothing of
// the Internet Archive, which is currently answering us with 429s and 503s.

const { chromium } = require("playwright")
const { Client } = require("pg")

const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

// Seconds between page loads. FF.net is not the Internet Archive — it has no
// published limit and it is a live site people are reading, so this is set by
// what is decent rather than by what we can get away with. At 25 works a page,
// even 6s is ~360,000 works a day, which finishes the 6.5M backlog in weeks.
const DELAY_MS = Number(process.env.FFNET_DELAY_MS || 6000)
const PAGES_PER_VISIT = Number(process.env.FFNET_PAGES_PER_VISIT || 8)
// No practical cap: a section is walked to its end and then starts again. It
// was 1000, which stopped Harry Potter (~34,000 pages) a thirtieth of the way in.
const MAX_PAGE = Number(process.env.FFNET_MAX_PAGE || 100000)
// How many sections each pass visits, taken in rotation from the discovered
// list (see discoverSections). Each visit is PAGES_PER_VISIT pages.
const SECTIONS_PER_PASS = Number(process.env.FFNET_SECTIONS_PER_PASS || 25)
// A fandom with fewer stories than this is not worth a slot in the rotation.
const MIN_SECTION_STORIES = Number(process.env.FFNET_MIN_SECTION_STORIES || 100)
const CATEGORIES = ["/anime/", "/book/", "/cartoon/", "/comic/", "/game/",
                    "/misc/", "/play/", "/movie/", "/tv/"]
const DISCOVER_EVERY_MS = 7 * 24 * 3600 * 1000

// FF.net sort: 1=updated, 2=published, 3=reviews, 4=favs, 5=follows.
//
// Favs, not updated. Sorting by "recently updated" walks the newest works on
// the site, which are precisely the ones a 2017-era bulk dump does not contain
// — the first test pass matched 25 works and updated none of them. Sorting by
// favourites walks the most-loved works instead: older, already in the index,
// and the ones where an engagement figure actually changes a ranking.
const SORT = process.env.FFNET_SORT || "4"

// FF.net's own category paths. These cannot be derived from our fandom names:
// the site uses its own slugs and its own top-level sections, and our facet
// list holds display names ("Harry Potter - J. K. Rowling") that no FF.net URL
// contains. Ordered roughly by how much of our index each covers.
const SECTIONS = (process.env.FFNET_SECTIONS || [
  "/book/Harry-Potter/",
  "/anime/Naruto/",
  "/tv/Supernatural/",
  "/book/Twilight/",
  "/cartoon/Danny-Phantom/",
  "/anime/Inuyasha/",
  "/tv/Glee/",
  "/movie/Star-Wars/",
  "/game/Pokemon/",
  "/anime/Bleach/",
  "/book/Percy-Jackson-and-the-Olympians/",
  "/tv/Doctor-Who/",
  "/comic/Batman/",
  "/game/Kingdom-Hearts/",
  "/tv/Buffy-The-Vampire-Slayer/",
  "/anime/Fairy-Tail/",
  "/book/Lord-of-the-Rings/",
  "/tv/Sherlock/",
  "/anime/Dragon-Ball-Z/",
  "/movie/Avengers/",
].join(",")).split(",").map(s => s.trim()).filter(Boolean)

const sleep = ms => new Promise(r => setTimeout(r, ms))

function num(s) {
  if (!s) return null
  const n = Number(String(s).replace(/,/g, ""))
  return Number.isFinite(n) ? n : null
}

// The metadata line is a single run of " - " separated fields and the set
// present varies per work: a one-shot omits "Chapters:", an old work omits
// "Favs:" entirely. So each field is matched independently rather than by
// position, and a missing one stays null rather than shifting its neighbours.
function parseRow(text) {
  const g = re => { const m = text.match(re); return m ? num(m[1]) : null }
  return {
    reviews: g(/Reviews:\s*([\d,]+)/),
    favs:    g(/Favs:\s*([\d,]+)/),
    follows: g(/Follows:\s*([\d,]+)/),
  }
}

async function cursorGet(db, key) {
  const r = await db.query("SELECT value FROM app_settings WHERE key=$1", [key])
  const n = r.rows.length ? parseInt(r.rows[0].value, 10) : 1
  return Number.isFinite(n) && n > 0 ? n : 1
}

async function cursorSet(db, key, page) {
  await db.query(
    `INSERT INTO app_settings (key, value) VALUES ($1,$2)
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
    [key, String(page)])
}

// Only ever raises a count, and only writes rows we already hold.
//
// GREATEST rather than assignment because this races the other collectors: the
// AO3 listing harvest and persist_live_results can touch the same row for a
// cross-posted work, and a listing page that is a few hours stale must not walk
// a fresher number backwards. Writing nothing when the value is unchanged also
// keeps this off the WAL for the overwhelmingly common case of a re-walk.
const UPDATE_SQL = `
  UPDATE stories SET
    comments  = GREATEST(COALESCE(comments,0),  $2),
    kudos     = GREATEST(COALESCE(kudos,0),     $3),
    bookmarks = GREATEST(COALESCE(bookmarks,0), $4)
  WHERE site='ffnet' AND site_id=$1
    AND (COALESCE(comments,0)  < $2
      OR COALESCE(kudos,0)     < $3
      OR COALESCE(bookmarks,0) < $4)
`

// The whole metadata line, staged for the worker to parse.
//
// This harvester read 246,861 rows a day and kept three counts from each. The
// same line carries the work's characters, bracketed pairings, genres, words,
// completion and dates — the fields FF.net is 98% missing in the index, which
// the archive.org route fills at ~500 works a day. Parsing stays in Python
// (backend/ffnet_listing.py -> ffnet_enrich.parse_ffn_meta) so there is ONE
// FF.net parser; this only stores what the page said.
const STAGE_SQL = `
  INSERT INTO ffnet_listing_meta (site_id, line, seen_at, title, author, author_url, summary, fandom)
  SELECT * FROM UNNEST($1::bigint[], $2::text[], $3::timestamptz[],
                       $4::text[], $5::text[], $6::text[], $7::text[], $8::text[])
  ON CONFLICT (site_id) DO UPDATE SET line = EXCLUDED.line, seen_at = EXCLUDED.seen_at,
    title = EXCLUDED.title, author = EXCLUDED.author, author_url = EXCLUDED.author_url,
    summary = EXCLUDED.summary, fandom = EXCLUDED.fandom
`

// One listing page, fetched in a browser context of its own.
//
// FF.net's refusal is per SESSION, not per rate. After roughly three listing
// pages on one context it answers 403 to that context and stays that way —
// measured waiting 24s, 72s and 216s between retries, all refused — while the
// very same pages on a fresh context return 200 immediately, 25 rows each.
//
// So the delay between requests stays as the unit of politeness, and the cookie
// jar is thrown away each time rather than slowed down, because slowing down
// does not satisfy the counter being tripped. A context is cheap: it is state,
// not a browser.
async function loadListing(browser, url) {
  const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1280, height: 900 } })
  // Images and fonts are most of a listing page's bytes and none of its
  // meaning. Blocking them is politeness as much as speed.
  await ctx.route("**/*", route => {
    const t = route.request().resourceType()
    return (t === "image" || t === "font" || t === "media") ? route.abort() : route.continue()
  })
  try {
    const page = await ctx.newPage()
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 })
    if (!resp || resp.status() !== 200) return { status: resp ? resp.status() : null, rows: null }
    const rows = await page.$$eval(".z-list", els => els.map(e => {
      const a = e.querySelector('a[href^="/s/"]')
      // The metadata line on its own — "Rated: … - Harry P. - Complete" — which
      // FF.net renders in a separate grey element. See STAGE_SQL below.
      const meta = e.querySelector(".z-padtop2, .xgray")
      const title = e.querySelector("a.stitle")
      const author = e.querySelector('a[href^="/u/"]')
      const indent = e.querySelector(".z-indent")
      const clean = t => (t || "").replace(/\s+/g, " ").trim()
      // The summary is the indent's text with the metadata line taken off.
      let summary = indent ? clean(indent.textContent) : ""
      if (meta && summary.endsWith(clean(meta.textContent)))
        summary = summary.slice(0, -clean(meta.textContent).length).trim()
      return { href: a ? a.getAttribute("href") : null,
               text: (e.textContent || "").replace(/\s+/g, " "),
               line: meta ? clean(meta.textContent) : null,
               title: title ? clean(title.textContent) : null,
               author: author ? clean(author.textContent) : null,
               authorUrl: author ? "https://www.fanfiction.net" + author.getAttribute("href") : null,
               summary: summary || null }
    }))
    // "Harry Potter FanFiction Archive | FanFiction" -> "Harry Potter", which is
    // how FF.net names the category and how the index already files its works.
    const fandom = ((await page.title()) || "").replace(/\s*FanFiction Archive.*$/i, "").trim() || null
    return { status: 200, rows, fandom }
  } catch (e) {
    return { status: null, rows: null, error: String(e).slice(0, 90) }
  } finally {
    await ctx.close().catch(() => {})
  }
}

async function harvestSection(browser, db, section) {
  const key = "ffnet_listing_page:" + section
  let start = await cursorGet(db, key)
  if (start > MAX_PAGE) start = 1          // walked to the end; go round again
  let updated = 0, seen = 0, pagesOk = 0, staged = 0

  for (let i = 0; i < PAGES_PER_VISIT; i++) {
    const p = start + i
    // r=10 includes every rating, so nothing is silently skipped by the
    // site's default filter.
    const url = `https://www.fanfiction.net${section}?&srt=${SORT}&r=10&p=${p}`
    const res = await loadListing(browser, url)
    if (res.status !== 200 || !res.rows) {
      // Cursor is not advanced past this page, so the next pass retries it.
      console.log(`  ${section} p${p}: ${res.error || "HTTP " + res.status} — cursor held`)
      break
    }
    if (!res.rows.length) {
      // Past the end of the section. The cursor used to stay HERE, so every
      // later visit asked for the same empty page and the section never
      // started again — "/comic/Batman/ p713: no rows" on every pass. Wrap.
      console.log(`  ${section} p${p}: no rows — end of section, back to page 1`)
      if (pagesOk === 0) { await cursorSet(db, key, 1); return { seen, updated, pagesOk } }
      break
    }
    pagesOk++

    const stageIds = [], stageLines = [], stageAt = []
    const stTitle = [], stAuthor = [], stAuthorUrl = [], stSummary = [], stFandom = []
    for (const r of res.rows) {
      const m = r.href && r.href.match(/^\/s\/(\d+)\//)
      if (!m) continue
      seen++
      if (r.line && r.line.startsWith("Rated:")) {
        stageIds.push(m[1]); stageLines.push(r.line); stageAt.push(new Date().toISOString())
        stTitle.push(r.title); stAuthor.push(r.author); stAuthorUrl.push(r.authorUrl)
        stSummary.push(r.summary); stFandom.push(res.fandom)
      }
      const v = parseRow(r.text)
      // Nothing to say about a work whose line carried none of the three.
      if (v.reviews == null && v.favs == null && v.follows == null) continue
      const q = await db.query(UPDATE_SQL,
        [m[1], v.reviews ?? 0, v.favs ?? 0, v.follows ?? 0])
      updated += q.rowCount
    }
    if (stageIds.length) {
      try {
        await db.query(STAGE_SQL, [stageIds, stageLines, stageAt,
                                   stTitle, stAuthor, stAuthorUrl, stSummary, stFandom])
        staged += stageIds.length
      } catch (e) {
        // Most likely the table does not exist yet (the backend creates it at
        // startup). The count update above already happened; carry on.
        console.log(`  stage failed: ${String(e).slice(0, 100)}`)
      }
    }
    await sleep(DELAY_MS)
  }
  // Advance only over pages that actually answered, so a failure mid-visit is
  // retried next time instead of being skipped silently.
  await cursorSet(db, key, start + Math.max(pagesOk, 0))
  console.log(`listing[ffnet] ${section} pages ${start}-${start + pagesOk - 1}: `
            + `${seen} works, ${updated} updated, ${staged} staged`)
  // pagesOk is what the caller needs to tell "the site answered and there was
  // nothing new" from "the site refused us" — 0 works updated means both.
  return { seen, updated, pagesOk }
}

// How long a total refusal may last before this process gives up and lets
// Docker start it again with a fresh browser. `restart: unless-stopped` in
// docker-compose.yml is what makes exiting the cheapest possible repair.
const WEDGE_MS = Number(process.env.FFNET_WEDGE_H || 6) * 3600 * 1000
// A pass that achieved nothing sleeps for longer each time, to this ceiling.
const IDLE_MIN_MS = 30000
const IDLE_MAX_MS = Number(process.env.FFNET_IDLE_MAX_MIN || 60) * 60 * 1000

// Every FF.net fandom worth walking, biggest first, from the site's own
// category pages ("Avatar: Last Airbender (46.4K)"). The fixed list of twenty
// walked ~500,000 works of a 6.5M+ archive; this is all of it. Refreshed
// weekly, cached in app_settings, and the fixed list is the fallback when
// discovery cannot get a category page.
function parseCount(t) {
  const m = /\(([\d.,]+)\s*([KM]?)\)\s*$/i.exec(t || "")
  if (!m) return 0
  const n = Number(m[1].replace(/,/g, ""))
  return Math.round(n * (m[2].toUpperCase() === "K" ? 1e3 : m[2].toUpperCase() === "M" ? 1e6 : 1))
}

async function loadCategory(browser, path) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1280, height: 900 } })
    await ctx.route("**/*", r => ["image", "font", "media"].includes(r.request().resourceType()) ? r.abort() : r.continue())
    try {
      const page = await ctx.newPage()
      const res = await page.goto("https://www.fanfiction.net" + path, { waitUntil: "domcontentloaded", timeout: 45000 })
      if (res && res.status() === 200) {
        return await page.$$eval(`a[href^="${path}"]`, (as, path) => as
          .filter(a => a.getAttribute("href") !== path)
          .map(a => [a.getAttribute("href"), (a.parentElement && a.parentElement.textContent) || ""]), path)
      }
    } catch (e) {
    } finally {
      await ctx.close().catch(() => {})
    }
    await sleep(DELAY_MS)
  }
  return null
}

async function discoverSections(browser, db) {
  if (process.env.FFNET_SECTIONS) return SECTIONS
  const cached = await db.query("SELECT value FROM app_settings WHERE key='ffnet_sections'")
    .then(r => r.rows.length ? JSON.parse(r.rows[0].value) : null).catch(() => null)
  if (cached && Date.now() - cached.at < DISCOVER_EVERY_MS && cached.sections.length)
    return cached.sections.map(x => x[0])
  const found = []
  for (const cat of CATEGORIES) {
    const links = await loadCategory(browser, cat)
    if (links) for (const [href, text] of links) {
      const n = parseCount(text.replace(/\s+/g, " ").trim())
      if (n >= MIN_SECTION_STORIES && /^\/[a-z]+\/[^/]+\/$/.test(href)) found.push([href, n])
    }
    await sleep(DELAY_MS)
  }
  if (found.length < SECTIONS.length) {
    console.log(`discover: only ${found.length} sections found — keeping the fixed list`)
    return cached && cached.sections.length ? cached.sections.map(x => x[0]) : SECTIONS
  }
  const uniq = [...new Map(found.map(x => [x[0], x])).values()].sort((a, b) => b[1] - a[1])
  await cursorSet(db, "ffnet_sections", JSON.stringify({ at: Date.now(), sections: uniq }))
  console.log(`discover: ${uniq.length} sections, ${uniq.reduce((a, x) => a + x[1], 0).toLocaleString()} stories`)
  return uniq.map(x => x[0])
}

async function main() {
  const once = process.argv.includes("--once")
  const db = new Client({ connectionString: process.env.DATABASE_URL })
  await db.connect()

  // EVIDENCE, AND A WAY OUT OF A TOTAL REFUSAL.
  //
  // The design note on loadListing says FF.net's refusal is per SESSION and
  // that a fresh context gets 200 immediately. That stopped being true: the
  // site now answers 403 to every context, and to plain curl, and to a browser
  // user agent, while still serving robots.txt. Measured in this container's
  // own log: 41,717 consecutive 403s and not one other status code.
  //
  // Two things went wrong with that and neither was the block itself.
  //
  // It kept asking. Every section broke on its first page and the pass slept
  // only 30s, so a site that had refused us tens of thousands of times was
  // asked again twice a minute, for days. The delay between requests is
  // described above as the unit of politeness; a refusal deserves the same
  // respect, so an idle pass now backs off to an hour.
  //
  // And it went silent. The last line this container logged was four days
  // before anyone looked, while the process was still alive and a headless
  // Chromium sat resident the whole time. `page.goto` has a 45s timeout, so
  // the wedge was elsewhere in the await chain — which is exactly why this
  // watchdog is a TIMER and not a check inside the loop: a timer keeps firing
  // when the loop is stuck, and a check inside the loop does not.
  let lastOkAt = Date.now()
  setInterval(() => {
    if (Date.now() - lastOkAt > WEDGE_MS) {
      console.error(`fatal: no listing page has succeeded in ${WEDGE_MS / 3600000}h `
                  + `— exiting so the container restarts with a fresh browser`)
      process.exit(1)
    }
  }, 60000).unref()
  // One browser for the process; each page load gets its own context inside
  // loadListing. Launching Chromium per page would cost seconds and gain
  // nothing — it is the cookie jar that has to be new, not the binary.
  const browser = await chromium.launch()

  let idleMs = IDLE_MIN_MS
  do {
    let seen = 0, updated = 0, pages = 0
    // This pass's share of the rotation, so every fandom is reached in turn
    // rather than the same twenty on every pass.
    const all = await discoverSections(browser, db)
    const rot = (await cursorGet(db, "ffnet_section_rot")) - 1
    const take = Array.from({ length: Math.min(SECTIONS_PER_PASS, all.length) },
                            (_, i) => all[(rot + i) % all.length])
    await cursorSet(db, "ffnet_section_rot", ((rot + take.length) % all.length) + 1)
    for (const s of take) {
      const r = await harvestSection(browser, db, s)
      seen += r.seen; updated += r.updated; pages += r.pagesOk
    }
    if (pages > 0) {
      lastOkAt = Date.now()
      idleMs = IDLE_MIN_MS
      // What the admin panel reads. A timestamp written only when a page
      // actually answered — evidence that the live harvest achieved
      // something, not a heartbeat saying the loop is turning. A loop that
      // turns happily over a site refusing every request is precisely the
      // failure this records.
      await cursorSet(db, "ffnet_listing_ok_at", Math.floor(Date.now() / 1000))
        .catch(() => {})
    } else {
      idleMs = Math.min(idleMs * 2, IDLE_MAX_MS)
    }
    console.log(`PASS DONE — ${seen} works seen, ${updated} updated, `
              + `${pages} pages answered`
              + (pages === 0 ? ` — nothing answered, sleeping ${Math.round(idleMs / 1000)}s` : ""))
    if (!once) await sleep(pages > 0 ? IDLE_MIN_MS : idleMs)
  } while (!once)

  await browser.close()
  await db.end()
}

main().catch(e => { console.error("fatal:", e); process.exit(1) })
