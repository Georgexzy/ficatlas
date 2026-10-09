// Drive the front door's browse explorer: do the rows go where they claim to?
//
// The change under test is a split in what a chip DOES. A fandom and a pairing
// have pages, so they are links to those pages; a tag has none, so it is a button
// that fills the search box. Both render from the same .hub-chips rules, so the
// risk is that the shared styling quietly made one of them unusable — a button
// that still navigates, or a link that fills the box — and that is only visible
// by clicking both and watching the URL.

const { chromium } = require("playwright");
const BASE = "https://127.0.0.1:8443";

const log = (...a) => console.log(...a);

async function chips(page) {
  return page.$$eval(".browse .hub-chips li", (ls) =>
    ls.map((li) => {
      const a = li.querySelector("a");
      const b = li.querySelector("button");
      return {
        label: (a || b)?.textContent?.trim(),
        href: a ? a.getAttribute("href") : null,
        isButton: !!b,
        pill: getComputedStyle(li).borderRadius,
        border: getComputedStyle(li).borderTopWidth,
        colour: (a || b) ? getComputedStyle(a || b).color : null,
      };
    }));
}

(async () => {
  const browser = await chromium.launch({ args: ["--ignore-certificate-errors", "--allow-insecure-localhost"] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector(".browse__tab", { timeout: 25000 });
  await page.waitForFunction(() => document.querySelectorAll(".browse .hub-chips li").length > 0, null, { timeout: 25000 });
  await page.waitForTimeout(400);

  const tabs = await page.$$eval(".browse__tab", (bs) =>
    bs.map((b) => ({ label: b.textContent.trim(), on: b.getAttribute("aria-selected") === "true" })));
  log("\ntabs: " + JSON.stringify(tabs));

  // ── 1. Pairings: chips must be LINKS to hub pages ──
  let cs = await chips(page);
  log(`\n1. PAIRINGS (default tab) — ${cs.length} chips`);
  log(`   all links: ${cs.every(c => c.href && !c.isButton)}`);
  log(`   sample: ${JSON.stringify(cs.slice(0, 3).map(c => ({ label: c.label, href: c.href })), null, 0)}`);
  log(`   pills (999px radius, 1px border): ${cs.every(c => c.pill === "999px" && c.border === "1px")}`);
  log(`   accent-coloured: ${cs.every(c => c.colour === "rgb(200, 169, 110)")}`);
  log(`   no bar chart left: ${(await page.$$(".browse__track")).length === 0}`);

  const firstHref = await page.$eval(".browse .hub-chips li a", (a) => a.getAttribute("href"));
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }),
    page.click(".browse .hub-chips li a"),
  ]);
  log(`   clicked ${firstHref} -> landed ${new URL(page.url()).pathname} (expected ${firstHref})`);
  const h1 = await page.$eval("h1", (e) => e.textContent.trim()).catch(() => null);
  const lede = await page.$eval(".hub__lede", (e) => e.textContent.replace(/\s+/g, " ").trim()).catch(() => null);
  log(`   landed on h1=${JSON.stringify(h1)}`);
  log(`   lede: ${JSON.stringify((lede || "").slice(0, 90))}`);

  // ── 2. The hub page's related nav still renders as pills after the CSS extraction ──
  const rel = await page.$$eval(".hub__related .hub-chips li", (ls) =>
    ls.slice(0, 3).map((li) => ({
      label: li.querySelector("a")?.textContent?.trim(),
      href: li.querySelector("a")?.getAttribute("href"),
      radius: getComputedStyle(li).borderRadius,
      countClass: li.querySelector("span")?.className,
    })));
  log(`\n2. HUB PAGE related nav — ${JSON.stringify(rel, null, 0)}`);

  // ── 3. Fandoms: also links ──
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll(".browse .hub-chips li").length > 0, null, { timeout: 25000 });
  await page.click('.browse__tab:has-text("Fandoms")');
  await page.waitForTimeout(500);
  cs = await chips(page);
  log(`\n3. FANDOMS — ${cs.length} chips, all links: ${cs.every(c => c.href && !c.isButton)}`);
  log(`   sample: ${JSON.stringify(cs.slice(0, 2).map(c => c.href))}`);
  log(`   footer: ${JSON.stringify((await page.$eval(".browse__more", (e) => e.textContent.replace(/\s+/g, " ").trim())))}`);

  // ── 4. Tags: must be BUTTONS, and must NOT navigate ──
  await page.click('.browse__tab:has-text("Tags")');
  await page.waitForTimeout(500);
  cs = await chips(page);
  log(`\n4. TAGS — ${cs.length} chips, all buttons: ${cs.every(c => c.isButton && !c.href)}`);
  log(`   footer: ${JSON.stringify((await page.$eval(".browse__more", (e) => e.textContent.replace(/\s+/g, " ").trim())))}`);

  const before = page.url();
  const box = await page.$eval("#search-q", (e) => {
    return { found: true, tag: e.tagName, id: e.id, cls: e.className };
  }).catch(() => ({ found: false }));
  log(`   search box: ${JSON.stringify(box)}`);
  await page.click(".browse .hub-chips li button");
  await page.waitForTimeout(1200);
  log(`   clicked a tag -> ${new URL(page.url()).pathname}${new URL(page.url()).search}  (path stayed ${new URL(before).pathname})`);
  const filled = await page.evaluate(() => {
    const el = document.querySelector("#search-q");
    return el ? el.value : null;
  });
  log(`   box now holds: ${JSON.stringify(filled)}`);

  // ── 5. A phone width: chips must wrap, not overflow ──
  await page.setViewportSize({ width: 360, height: 780 });
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(() => ({
    docScroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
    chipsWidth: Math.max(0, ...[...document.querySelectorAll(".browse .hub-chips li")].map(l => l.getBoundingClientRect().width)),
  }));
  log(`\n5. 360px — clientWidth ${overflow.client}, scrollWidth ${overflow.docScroll}, widest chip ${Math.round(overflow.chipsWidth)}px`);
  log(`   sideways scroll: ${overflow.docScroll > overflow.client}`);

  await browser.close();
})();