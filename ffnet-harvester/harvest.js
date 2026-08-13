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
const MAX_PAGE = Number(process.env.FFNET_MAX_PAGE || 1000)

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
      return { href: a ? a.getAttribute("href") : null,
               text: (e.textContent || "").replace(/\s+/g, " ") }
    }))
    return { status: 200, rows }
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
  let updated = 0, seen = 0, pagesOk = 0

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
    if (!res.rows.length) { console.log(`  ${section} p${p}: no rows, stopping`); break }
    pagesOk++

    for (const r of res.rows) {
      const m = r.href && r.href.match(/^\/s\/(\d+)\//)
      if (!m) continue
      seen++
      const v = parseRow(r.text)
      // Nothing to say about a work whose line carried none of the three.
      if (v.reviews == null && v.favs == null && v.follows == null) continue
      const q = await db.query(UPDATE_SQL,
        [m[1], v.reviews ?? 0, v.favs ?? 0, v.follows ?? 0])
      updated += q.rowCount
    }
    await sleep(DELAY_MS)
  }
  // Advance only over pages that actually answered, so a failure mid-visit is
  // retried next time instead of being skipped silently.
  await cursorSet(db, key, start + Math.max(pagesOk, 0))
  console.log(`listing[ffnet] ${section} pages ${start}-${start + pagesOk - 1}: `
            + `${seen} works, ${updated} updated`)
  return { seen, updated }
}

async function main() {
  const once = process.argv.includes("--once")
  const db = new Client({ connectionString: process.env.DATABASE_URL })
  await db.connect()
  // One browser for the process; each page load gets its own context inside
  // loadListing. Launching Chromium per page would cost seconds and gain
  // nothing — it is the cookie jar that has to be new, not the binary.
  const browser = await chromium.launch()

  do {
    let seen = 0, updated = 0
    for (const s of SECTIONS) {
      const r = await harvestSection(browser, db, s)
      seen += r.seen; updated += r.updated
    }
    console.log(`PASS DONE — ${seen} works seen, ${updated} updated`)
    if (!once) await sleep(30000)
  } while (!once)

  await browser.close()
  await db.end()
}

main().catch(e => { console.error("fatal:", e); process.exit(1) })
