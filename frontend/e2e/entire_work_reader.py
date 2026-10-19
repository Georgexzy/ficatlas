"""Drive the entire-work reader in a real browser and check it does what it
claims: renders every chapter, tracks where the reader is, jumps, pages in the
tail, records a failed page and retries it, and does not crash.

WHY THIS FILE EXISTS
--------------------
The reader's logic is spread across effects that only make sense against a real
document — refs registering as sections mount, a smooth scroll that retargets,
a debounced save, a position restore that lands on a heading, pages arriving
underneath the reader while it is being read. None of it is reachable from
`lib/`-style unit tests (vitest here deliberately does not render React), so
without this the entire-work view shipped as an unverified claim about what the
code does.

RUNNING IT
----------
1. Playwright, in a venv of its own (the repo has no JS test deps beyond vitest):

       python3 -m venv /tmp/opencode/pw
       /tmp/opencode/pw/bin/pip install playwright
       PLAYWRIGHT_BROWSERS_PATH=/tmp/opencode/pw-browsers \
           /tmp/opencode/pw/bin/playwright install chromium

2. A frontend build whose /api rewrite points at an API that HAS the
   `/{story_id}/entire` route — the deployed one may not. Build and run it on
   the host network so its rewrite reaches a locally running backend:

       docker build -q --build-arg FORCE_HTTPS=true \
         --build-arg INTERNAL_API_URL=http://127.0.0.1:8000 \
         --build-arg NEXT_PUBLIC_SITE_URL=https://ficatlas.com \
         -t ficatlas-frontend:e2e ./frontend
       docker run -d --rm --name e2e-web --network host -e PORT=3100 \
         ficatlas-frontend:e2e

3. Open port 3100's base URL in the script (BASE, below) and run:

       PLAYWRIGHT_BROWSERS_PATH=/tmp/opencode/pw-browsers \
           /tmp/opencode/pw/bin/python frontend/e2e/entire_work_reader.py

It reads real hosted works out of the local database, by id, and only ever
issues GETs. Exits non-zero on any failure or any JS error."""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3100"
SMALL = "104ab4c4-af60-4fd1-a2a9-689f447bdcfc"   # 8 chapters, one page
BIG = "4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c"     # 199 chapters, 8 pages

fails = []


def crumbs_of(page):
    return page.locator(".reader__breadcrumb").inner_text().replace("\n", " ")


def wait_crumbs(page, expect, timeout=5000):
    """Read the breadcrumb until it says `expect`.

    Every move here is a smooth scroll, and Chrome's duration scales with the
    distance — eight thousand pixels is well over a second. A fixed wait either
    wastes it on short moves or samples a long one in flight and reports the
    product as broken when it is only still travelling.
    """
    deadline, txt = time.time() + timeout, ""
    while time.time() < deadline:
        txt = crumbs_of(page)
        if expect.lower() in txt.lower():
            return txt
        page.wait_for_timeout(200)
    return txt


def check(name, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{extra}]" if extra else ""))
    if not ok:
        fails.append(name)


def wait_scroll_stable(page, timeout=8000):
    """Wait until scrolling has stopped.

    `scrollToCh` sets `currentCh` (and so the breadcrumb) the instant it is
    called, deliberately — the label is a statement of intent, not of
    animation — so a check that trusts the label and reads the scroll position
    reads it while the smooth scroll is still at the top and reports that
    nothing moved.
    """
    deadline, last, still = time.time() + timeout, -1, 0
    y = 0
    while time.time() < deadline:
        y = page.evaluate("window.scrollY")
        if y == last:
            still += 1
            if still >= 3:
                return y
        else:
            still, last = 0, y
        page.wait_for_timeout(150)
    return y


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.on("console",
            lambda m: errs.append(f"console.error: {m.text}") if m.type == "error" else None)

    # ── The whole work, in one page ──────────────────────────────────────────
    page.goto(f"{BASE}/story/{SMALL}/entire", wait_until="load")
    page.wait_for_selector("section.reader__part", timeout=20000)
    n = page.locator("section.reader__part").count()
    check("every chapter rendered", n == 8, f"{n} sections")
    # Settle: the sections register their refs, and the position restore (if this
    # browser has read this work before) smooth-scrolls to it. Both happen in the
    # first frames after hydration, and a control used before they finish is a
    # control that silently does nothing — which is a test artefact, not a bug,
    # so the test waits like a reader would.
    page.wait_for_timeout(1500)
    crumbs = page.locator(".reader__breadcrumb").inner_text()
    check("breadcrumb says which view", "entire work" in crumbs.lower(),
          crumbs.replace("\n", " ")[:70])
    check("byline on the document", page.locator(".reader__byline").count() == 1)
    check("work summary above the text", page.locator(".reader__summary").count() >= 1)
    opts = page.locator(".reader-nav__jump option").count()
    check("jump list has every chapter", opts == 8, f"{opts} options")
    check("a way back to chapter-by-chapter",
          page.locator("a.reader-nav__index", has_text="Chapter by chapter").count() == 1)
    check("no loading footer when it all fitted",
          page.locator(".reader-more").count() == 0)
    ids = page.eval_on_selector_all(
        "section.reader__part", "els => els.map(e => e.id)")
    check("chapter anchors are unique and ordered",
          ids == [f"ch-{i}" for i in range(1, 9)], str(ids))
    first_text = page.locator("section.reader__part .reader__content").first.inner_text()
    check("text is on the page, not a spinner",
          len(first_text) > 50 and "Loading the work" not in first_text,
          f"{len(first_text)} chars")

    # ── Jumping, by the select and by the keyboard ──────────────────────────
    page.select_option(".reader-nav__jump", "5")
    crumbs = wait_crumbs(page, "chapter 5 of 8")
    y_after_jump = wait_scroll_stable(page)
    check("jump lands on chapter 5 and the breadcrumb follows",
          "chapter 5 of 8" in crumbs.lower(), crumbs[:70])
    check("jump actually scrolled", y_after_jump > 2000, f"scrollY={y_after_jump}")

    anchor = page.evaluate(
        "document.activeElement && document.activeElement.closest('section.reader__part')?.id || ''")
    check("focus moved into the chapter it jumped to", anchor == "ch-5", f"focus in {anchor!r}")

    y_before_key = page.evaluate("window.scrollY")
    # The select keeps keyboard focus after select_option, and ← → on a focused
    # <select> is the browser's own typeahead, not ours. Blur rather than click:
    # anything clickable near the top of this page is a link.
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("ArrowRight")
    crumbs = wait_crumbs(page, "chapter 6 of 8")
    y_after_key = wait_scroll_stable(page)
    check("ArrowRight turns to the next chapter", y_after_key > y_before_key,
          f"{y_before_key} -> {y_after_key}")
    check("and the breadcrumb follows it", "chapter 6 of 8" in crumbs.lower(), crumbs[:70])

    # ── Scrolling updates the reading line, the bar, and the record ─────────
    page.evaluate("window.scrollTo(0, 0)")
    wait_scroll_stable(page)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight * 0.6)")
    wait_scroll_stable(page)
    page.wait_for_timeout(800)   # the save is debounced by 600ms
    crumbs = crumbs_of(page)
    bar = page.locator(".reader-progress").get_attribute("style") or ""
    check("breadcrumb tracks the scroll", "chapter" in crumbs.lower(), crumbs.replace("\n", " ")[:70])
    check("progress bar advances", "0%" not in bar, bar)

    prog = page.evaluate(
        "JSON.parse(localStorage.getItem('ficatlas:progress') || '{}')")
    rec = prog.get(SMALL)
    check("position recorded under the shared key",
          bool(rec) and isinstance(rec.get("chapter"), int),
          str({k: rec.get(k) for k in ("chapter", "scrollPct", "totalChapters")}) if rec else "absent")
    check("position names a real chapter with a fraction",
          bool(rec) and 0 < (rec.get("positions", {}).get(str(rec.get("chapter"))) or -1) < 1,
          str(rec.get("positions")) if rec else "")

    # ── Coming back resumes ─────────────────────────────────────────────────
    before_ch = crumbs
    page.reload(wait_until="load")
    page.wait_for_selector("section.reader__part", timeout=20000)
    crumbs = wait_crumbs(page, before_ch)   # the restore sets the label as it moves
    wait_scroll_stable(page)
    y_after_reload = page.evaluate("window.scrollY")
    check("reload resumes at the same chapter", crumbs == before_ch,
          f"{before_ch.replace(chr(10), ' ')} -> {crumbs.replace(chr(10), ' ')}")
    check("…and not at the top", y_after_reload > 500, f"scrollY={y_after_reload}")

    # ── A work too big to send at once ──────────────────────────────────────
    page.goto(f"{BASE}/story/{BIG}/entire", wait_until="load")
    page.wait_for_selector("section.reader__part", timeout=20000)
    check("the outline marks chapters whose text has not arrived",
          page.eval_on_selector_all("option:disabled", "els => els.length") > 0,
          f"{page.eval_on_selector_all('option:disabled', 'els => els.length')} pending")
    check("a still-loading tail says so",
          page.locator(".reader-more").count() == 1)
    deadline = time.time() + 45
    while time.time() < deadline:
        c = page.locator("section.reader__part").count()
        if c >= 199:
            break
        page.wait_for_timeout(500)
    n = page.locator("section.reader__part").count()
    check("199 chapters streamed in", n == 199, f"{n} after 45s")
    ids = page.eval_on_selector_all("section.reader__part", "els => els.map(e => e.id)")
    check("no chapter arrived twice", len(set(ids)) == len(ids) == 199, f"{len(set(ids))}")
    check("the tail closed when it finished", page.locator(".reader-more").count() == 0)

    # ── A page that will not come ───────────────────────────────────────────
    blocked = {"on": True}

    def block_tail(route):
        url = route.request.url
        if blocked["on"] and "after=" in url and not url.endswith("after=0"):
            route.fulfill(status=500, body="nope")
        else:
            route.continue_()

    page.route("**/api/stories/**", block_tail)
    page.goto(f"{BASE}/story/{BIG}/entire", wait_until="load")
    page.wait_for_selector(".reader-more", timeout=30000)
    held = page.locator("section.reader__part").count()
    check("a page that fails says so rather than spinning",
          "load the rest" in page.locator(".reader-more").inner_text().lower())
    check("and takes nothing away from what arrived", held > 1, f"{held} chapters still shown")

    blocked["on"] = False
    page.locator(".reader-more button").click()
    deadline = time.time() + 90
    while time.time() < deadline and page.locator("section.reader__part").count() < 199:
        page.wait_for_timeout(500)
    check("retrying the rest finishes the work",
          page.locator("section.reader__part").count() == 199)
    page.unroute("**/api/stories/**")

    # ── What the listen button promises ─────────────────────────────────────
    page.goto(f"{BASE}/story/{SMALL}/entire", wait_until="load")
    page.wait_for_selector("section.reader__part", timeout=20000)
    label = page.locator(".reader-controls button").first.get_attribute("aria-label")
    check("listening reads the whole work, and says so",
          label == "Listen to the whole work", str(label))

    page.goto(f"{BASE}/story/{SMALL}/chapter/3", wait_until="load")
    page.wait_for_selector("#reader-body", timeout=20000)
    label = page.locator(".reader-controls button").first.get_attribute("aria-label")
    check("the chapter reader still listens to one chapter",
          label == "Listen to this chapter", str(label))

    # ── The single-chapter work, which has no work to concatenate ───────────
    ONE = "49dfcc12-a188-42af-9923-401b8884351a"
    page.goto(f"{BASE}/story/{ONE}", wait_until="load")
    page.wait_for_selector(".chapter-list", timeout=20000)
    check("a single-chapter work offers no entire-work mode",
          page.locator(".chapter-list__entire").count() == 0)

    # ── The single-chapter reader is untouched ──────────────────────────────
    page.goto(f"{BASE}/story/{SMALL}/chapter/3", wait_until="load")
    page.wait_for_selector("#reader-body", timeout=20000)
    body = page.locator("#reader-body").inner_text()
    check("chapter reader still serves one chapter",
          len(body) > 50 and page.locator("section.reader__part").count() == 0,
          f"{len(body)} chars")
    check("chapter reader offers the way into the whole work",
          page.locator("a.reader-nav__index", has_text="Entire work").count() == 1)

    browser.close()

print()
# The 500 is the one the stalled-page test injects on purpose; anything else is
# the product.
real_errors = [e for e in errs
               if "favicon" not in e.lower() and "status of 500" not in e.lower()]
if real_errors:
    print("JS ERRORS:")
    for e in real_errors[:10]:
        print("   ", e[:300])
else:
    print("no JS errors or console errors")

print(f"\n{'ALL PASS' if not fails and not real_errors else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails or real_errors else 0)
