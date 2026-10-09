// Does an offline reader survive a deploy?
//
// This is the question the change was made for, and it is the one a single-page
// test cannot answer: it needs a build to LAND between two visits.
//
// Setup is two builds of the same app that differ in nothing but the service
// worker's cache name — genA and genB, the second derived from the first by a
// sed of the stamp. So any difference in behaviour is attributable to the deploy
// and not to a code change.
//
// The claim being tested: `activate` used to delete every cache but the current
// one, which threw away every page the reader had opened, several times a day.
// KEEP_GENERATIONS = 2 should keep the previous generation AND the reads should
// still find it — and the second half is the one that can fail silently, because
// an old page served WITHOUT the old chunks it references renders as markup and
// never boots.

const { chromium } = require("playwright");
const BASE = "https://127.0.0.1:8443";
const PAGE = "/fandom/harry-potter";   // ~400 KB, opened once, never revisited

const log = (...a) => console.log(...a);
const snip = (t, n = 100) => JSON.stringify((t || "").replace(/\s+/g, " ").slice(0, n));

async function cacheNames(page) {
  return page.evaluate(() => caches.keys());
}
async function entries(page, name) {
  return page.evaluate(async (n) => {
    const c = await caches.open(n);
    const keys = await c.keys();
    return keys.map((r) => new URL(r.url).pathname).filter((p) => !p.startsWith("/_next/"));
  }, name);
}

async function open(page, path, offline) {
  if (offline) await page.context().setOffline(true);
  let failed = null;
  try { await page.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 25000 }); }
  catch (e) { failed = String(e).split("\n")[0].slice(0, 60); }
  await page.waitForTimeout(2000);
  if (offline) await page.context().setOffline(false);
  return failed;
}

(async () => {
  // --disk-cache-size=1 is load-bearing, and finding out why took three runs.
//
// Playwright's `context.setOffline(true)` does NOT reach the service worker's own
// `fetch()` calls, so a URL that was fetched over HTTP moments ago is still
// served — from Chromium's HTTP disk cache, by the worker, as though it were a
// cache hit. A deleted cache generation therefore still "works" and the test
// cannot tell KEEP_GENERATIONS=1 from 2. Disabling the disk cache via CDP does
// not help either: that setting is per-target and the fetch is made by the
// WORKER's target. Shrinking the disk cache to nothing works for both.
  const browser = await chromium.launch({
    args: ["--ignore-certificate-errors", "--allow-insecure-localhost", "--disk-cache-size=1"],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();

  // Chromium's HTTP disk cache OFF. It is a confound that makes this test lie:
  // a page loaded moments ago is served from it even with no network and no
  // Cache Storage entry, so a deleted generation still "works" and the run
  // cannot tell KEEP_GENERATIONS=1 from 2. The point of the change is what the
  // SERVICE WORKER can still reach, so the browser's own cache has to be out of
  // the picture.
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });

  // ── install on genA, and open a page ──
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.evaluate(async () => navigator.serviceWorker.ready);
  await page.waitForTimeout(2500);
  const namesA = await cacheNames(page);
  log(`\n1. on genA — caches: ${JSON.stringify(namesA)}`);
  log(`   KEEP_GENERATIONS in the served worker: ${await page.evaluate(async () => {
    const r = await fetch("/sw.js"); const t = await r.text();
    return (t.match(/const KEEP_GENERATIONS = (\d+)/) || [])[1];
  })}`);

  await open(page, PAGE, false);
  const afterVisit = await cacheNames(page);
  const genA = afterVisit.find((n) => n.includes("2026"));
  log(`\n2. opened ${PAGE} online`);
  log(`   it is cached in: ${JSON.stringify(await entries(page, genA))}`);

  // ── the deploy. The caller swaps the container between steps; this waits. ──
  log("\n3. waiting for genB to be served…");
  let deployed = false;
  for (let i = 0; i < 60; i++) {
    // Ask the CACHE LIST rather than fetching /sw.js. Asking the worker which
    // build it is was the first thing tried and it answers with the cached copy
    // of sw.js — the extension rule claimed that file as a hashed asset, so the
    // worker was caching itself. That is fixed in the template; the cache list
    // is the direct observation anyway.
    // Reload, or nothing happens: the browser checks for a worker update on a
    // NAVIGATION into scope (and on a 24h timer). Polling caches.keys() from a
    // page that never navigates waits forever for a worker that is never asked.
    await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const names = await cacheNames(page).catch(() => []);
    const stamp = names.find((n) => n.includes("2099"));
    if (stamp) { deployed = true; log(`   new generation active: ${stamp}`); break; }
    await page.waitForTimeout(2000);
  }
  if (!deployed) { log("   genB never appeared"); await browser.close(); return; }

  // Reload so the new worker installs and activates.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000);
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.waitForTimeout(4000);

  const namesB = await cacheNames(page);
  log(`\n4. after the deploy — caches: ${JSON.stringify(namesB)}`);
  log(`   previous generation retained: ${namesB.includes(genA)}`);
  const genB = namesB.find((n) => n.includes("2099"));
  log(`   previous gen still holds ${PAGE}: ${(await entries(page, genA)).includes(PAGE)}`);

  // ── the actual claim ──
  // Wait for a REAL outage before measuring anything. `setOffline()` is not one:
  // it does not reach the worker's own fetches, so a URL it can re-fetch comes
  // back 200 (or 404) and the run proves nothing. The test therefore blocks until
  // an uncached request genuinely FAILS, which is the only definition of offline
  // that matters to a service worker.
  log(`\n   waiting for the network to actually go away…`);
  let severed = false;
  for (let i = 0; i < 45; i++) {
    const r = await page.evaluate(async () => {
      try { const x = await fetch("/never-fetched-" + Math.random(), { cache: "no-store" }); return "ok:" + x.status }
      catch (e) { return "dead" }
    }).catch(() => "dead");
    if (r === "dead") { severed = true; log(`   severed after ~${i * 2}s`); break; }
    await page.waitForTimeout(2000);
  }
  if (!severed) { log("   network never actually went away — measurement aborted"); await browser.close(); return; }

  log(`\n5. OFFLINE, the page opened before the deploy:`);
  const f1 = await open(page, PAGE, true);
  const s1 = await page.evaluate(() => ({
    text: (document.body.innerText || "").replace(/\s+/g, " "),
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    // A shell whose JS 404s still has an h1. Boot is the real test.
    booted: !!document.querySelector("[data-nextjs-router], #__next, script[src*='_next']"),
    links: document.querySelectorAll("a").length,
  })).catch(() => null);
  log(`   navigation failed: ${f1}`);
  log(`   h1: ${JSON.stringify(s1?.h1)}`);
  log(`   anchors: ${s1?.links}  (0 means markup rendered but nothing hydrated)`);
  log(`   text: ${snip(s1?.text, 130)}`);

  log(`\n6. OFFLINE, a page precached by the CURRENT generation:`);
  const f2 = await open(page, "/about", true);
  const s2 = await page.evaluate(() => ({
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    links: document.querySelectorAll("a").length,
  })).catch(() => null);
  log(`   navigation failed: ${f2}  h1: ${JSON.stringify(s2?.h1)}  anchors: ${s2?.links}`);

  log(`\n7. OFFLINE, the library (precached in both generations):`);
  const f3 = await open(page, "/library", true);
  const s3 = await page.evaluate(() => ({
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    text: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 80),
  })).catch(() => null);
  log(`   navigation failed: ${f3}  h1: ${JSON.stringify(s3?.h1)}`);
  log(`   ${snip(s3?.text)}`);

  await browser.close();
})();