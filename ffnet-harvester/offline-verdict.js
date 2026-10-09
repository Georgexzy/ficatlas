// The one measurement the earlier runs could not make: with the network REALLY
// gone, can the reader still reach a page they opened before the deploy?
//
// `setOffline()` does not reach the service worker's fetches, so the test waits
// for a genuine outage instead, and the harness severs the container from the
// docker network to cause one. Disk cache is shrunk to nothing for the same
// reason: a page fetched over HTTP seconds ago would otherwise be served from
// it by the worker and both configurations would look identical.

const { chromium } = require("playwright");
const BASE = "https://127.0.0.1:8443";
const PAGE = "/fandom/harry-potter";

const log = (...a) => console.log(...a);

(async () => {
  const browser = await chromium.launch({
    args: ["--ignore-certificate-errors", "--allow-insecure-localhost", "--disk-cache-size=1"],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.evaluate(async () => navigator.serviceWorker.ready);
  await page.waitForTimeout(2500);
  const k = await page.evaluate(async () => {
    const t = await (await fetch("/sw.js", { cache: "no-store" })).text();
    return (t.match(/const KEEP_GENERATIONS = (\d+)/) || [])[1];
  });
  log(`\nKEEP_GENERATIONS in the worker that will be active: ${k}`);

  await page.goto(BASE + PAGE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(2000);
  const genA = (await page.evaluate(() => caches.keys())).find((n) => n.includes("2026"));
  log(`opened ${PAGE} on ${genA}`);

  // LEAVE the page before waiting for the deploy, and this is the whole ballgame.
  // The deploy is detected by reloading, and a reload of ${PAGE} would re-fetch
  // it into the NEW generation — which is exactly what a reader does NOT do. Two
  // earlier runs of this test "passed" with KEEP_GENERATIONS=1 for that reason
  // alone: the page under test had been quietly refreshed into the cache the fix
  // is about.
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(1500);
  log(`navigated away to / — ${PAGE} now exists only in the old generation`);

  log("waiting for the deploy…");
  let active = null;
  for (let i = 0; i < 60; i++) {
    await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1500);
    active = (await page.evaluate(() => caches.keys()).catch(() => [])).find((n) => n.includes("2099"));
    if (active) break;
  }
  if (!active) { log("no deploy detected"); await browser.close(); return; }
  const names = await page.evaluate(() => caches.keys());
  log(`deploy landed on ${active}`);
  log(`caches now: ${JSON.stringify(names)}`);
  log(`previous generation retained: ${names.includes(genA)}`);

  log("waiting for the network to actually go away…");
  let severed = false;
  for (let i = 0; i < 45; i++) {
    const r = await page.evaluate(async () => {
      try { return "ok:" + (await fetch("/never-" + Math.random(), { cache: "no-store" })).status }
      catch { return "dead" }
    }).catch(() => "dead");
    if (r === "dead") { severed = true; log(`severed after ~${i * 2}s`); break; }
    await page.waitForTimeout(2000);
  }
  if (!severed) { log("network never went away — aborted"); await browser.close(); return; }

  log(`\nOFFLINE, ${PAGE} — the page opened before the deploy:`);
  await page.goto(BASE + PAGE, { waitUntil: "domcontentloaded", timeout: 25000 }).catch((e) =>
    log(`  navigation failed: ${String(e).split("\n")[0].slice(0, 60)}`));
  await page.waitForTimeout(2500);
  const s = await page.evaluate(() => ({
    h1: document.querySelector("h1")?.textContent?.trim() ?? null,
    anchors: document.querySelectorAll("a").length,
    text: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 90),
  })).catch(() => null);
  log(`  h1=${JSON.stringify(s?.h1)}  anchors=${s?.anchors}`);
  log(`  ${JSON.stringify(s?.text)}`);
  const ok = !!s?.h1 && s.anchors > 0;
  log(`\n  VERDICT: ${ok ? "REACHABLE offline after a deploy" : "LOST at the deploy"}`);

  await browser.close();
})();