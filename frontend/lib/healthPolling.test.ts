/** The health poll must be ONE self-re-arming chain, however often a tab is focused.
 *
 * `HealthBanner` re-arms itself in its own `finally` and is also called
 * directly by a focus/online handler. Before the fix that direct call did not
 * replace the scheduled poll, it ADDED to it: the pending timeout still fired
 * and started a second chain, the two never merged, and a tab accumulated one
 * chain per focus event for as long as it stayed open.
 *
 * Measured at the origin: 7,294 requests to /api/stats/totals in a single
 * hour — two a second — against the two a minute one chain costs. None of it
 * is absorbed at the edge, because the poll asks for `no-store` on purpose (a
 * health check served from a 300s cache cannot see an outage), so all of it
 * reached the box over a domestic connection and queued beside real searches.
 *
 * A source assertion rather than a render test: this suite deliberately does
 * not mount React (see vitest.config.ts), and the failure needs a long-lived
 * tab and repeated focus events to show up at all. Same shape, and the same
 * reasoning, as backend/tests/test_maintenance_timeouts.py.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SRC = readFileSync(
  path.resolve(__dirname, "../app/HealthBanner.tsx"), "utf8")

describe("HealthBanner polling", () => {
  it("cancels the pending poll before starting another", () => {
    expect(SRC).toContain("clearTimeout(timer)")
    // ...and does it INSIDE check, ahead of the re-arm, not only in cleanup.
    const check = SRC.indexOf("const check = async")
    const clear = SRC.indexOf("clearTimeout(timer)", check)
    const rearm = SRC.indexOf("timer = setTimeout(check", check)
    expect(check).toBeGreaterThan(-1)
    expect(clear).toBeGreaterThan(check)
    expect(rearm).toBeGreaterThan(clear)
  })

  it("throttles the focus/online handler", () => {
    const wake = SRC.slice(SRC.indexOf("const wake ="),
                           SRC.indexOf("window.addEventListener(\"online\""))
    expect(wake).toMatch(/Date\.now\(\)\s*-\s*last/)
    expect(wake).toMatch(/return/)
  })

  it("still polls on focus at all", () => {
    // The throttle must not become a removal: a reader coming back after an
    // outage should see the banner clear promptly, which is the whole reason
    // the handler exists.
    expect(SRC).toContain('window.addEventListener("focus", wake)')
    expect(SRC).toContain('window.addEventListener("online", wake)')
  })

  it("keeps the poll uncacheable", () => {
    // If this ever becomes cacheable the banner stops being a health check and
    // starts being a report on what the edge remembered five minutes ago.
    expect(SRC).toContain('cache: "no-store"')
  })
})
