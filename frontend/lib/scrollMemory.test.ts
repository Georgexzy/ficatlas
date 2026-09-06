import { beforeEach, describe, expect, it, vi } from "vitest"
import { clearScroll, restoreScroll, saveScroll, takeScroll } from "./scrollMemory"

// Where the reader was in their results.
//
// The failure this file is written against is the one that shipped: a restore
// that "succeeded" against a document too short to reach the position, and
// cleared the memory on the way past. It is silent — the reader lands at the
// top of the results and there is nothing left to try again from — and it is
// invisible to any test that only checks the happy path, because the happy path
// is the one where the page happens to have finished rendering first.

const KEY = "ficatlas:scroll-memory"

/** A document of a given height in a 900px viewport, scrolled to `y`. */
function layout(scrollHeight: number, y = 0) {
  Object.defineProperty(document.documentElement, "scrollHeight",
    { value: scrollHeight, configurable: true })
  Object.defineProperty(window, "innerHeight", { value: 900, configurable: true })
  let at = y
  Object.defineProperty(window, "scrollY", { get: () => at, configurable: true })
  const scrollTo = vi.fn((opts: any) => {
    // The browser clamps: you cannot scroll past the end of the document.
    const top = typeof opts === "number" ? opts : opts.top
    at = Math.min(top, Math.max(0, scrollHeight - 900))
  })
  Object.defineProperty(window, "scrollTo", { value: scrollTo, configurable: true })
  return scrollTo
}

/** Let the rAF retry loop run a few times. */
const frames = () => new Promise(r => setTimeout(r, 60))

beforeEach(() => {
  sessionStorage.clear()
})

describe("saveScroll / takeScroll", () => {
  it("round-trips a position for one exact URL", () => {
    saveScroll("/?q=drarry", 1400)
    expect(takeScroll("/?q=drarry")).toBe(1400)
  })

  it("does not hand a position to a different search", () => {
    saveScroll("/?q=drarry", 1400)
    expect(takeScroll("/?q=wolfstar")).toBeNull()
  })
})

describe("restoreScroll", () => {
  it("scrolls to the remembered position once the page can reach it", async () => {
    saveScroll("/?q=drarry", 1400)
    const scrollTo = layout(6189)
    restoreScroll("/?q=drarry")
    await frames()
    expect(scrollTo).toHaveBeenCalledWith({ top: 1400, left: 0, behavior: "instant" })
    expect(window.scrollY).toBe(1400)
  })

  it("does NOT scroll, and does not forget, while the page is too short", async () => {
    // The measured case: a back-navigation runs this while the 1,882px page
    // being LEFT is still the one laid out. `scrollHeight > y` passed here
    // (1,882 > 1,400), the scroll was clamped to 982, and the entry was thrown
    // away as though it had worked.
    saveScroll("/?q=drarry", 1400)
    const scrollTo = layout(1882)
    restoreScroll("/?q=drarry")
    await frames()
    expect(scrollTo).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(KEY)).toContain("1400")
  })

  it("keeps the position after a successful restore", async () => {
    // Consuming it lost the position outright whenever the reader was already
    // at that height: scrolling to where you already are fires no scroll event,
    // so nothing wrote it back.
    saveScroll("/?q=drarry", 1400)
    layout(6189, 1400)
    restoreScroll("/?q=drarry")
    await frames()
    expect(sessionStorage.getItem(KEY)).toContain("1400")
  })

  it("ignores an entry belonging to a different URL", async () => {
    saveScroll("/?q=drarry", 1400)
    const scrollTo = layout(6189)
    restoreScroll("/?q=wolfstar")
    await frames()
    expect(scrollTo).not.toHaveBeenCalled()
  })

  it("suppresses saves while it is still trying", async () => {
    // The browser's own back-navigation scroll lands wherever it likes — the
    // very bottom of the page, measured — and the page records every scroll it
    // sees. Without this it overwrote the position being restored TO.
    saveScroll("/?q=drarry", 1400)
    layout(1882)                 // too short: the attempt stays in flight
    restoreScroll("/?q=drarry")
    saveScroll("/?q=drarry", 5289)
    expect(sessionStorage.getItem(KEY)).toContain("1400")
    await frames()
  })

  it("lets saves through again once it gives up", async () => {
    saveScroll("/?q=drarry", 1400)
    layout(1882)
    restoreScroll("/?q=drarry")
    // ~120 frames at happy-dom's rAF cadence.
    await new Promise(r => setTimeout(r, 900))
    saveScroll("/?q=drarry", 800)
    expect(sessionStorage.getItem(KEY)).toContain("800")
  })
})

describe("clearScroll", () => {
  it("forgets the position for that URL", () => {
    saveScroll("/?q=drarry", 1400)
    clearScroll("/?q=drarry")
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })

  it("leaves another search's position alone", () => {
    saveScroll("/?q=drarry", 1400)
    clearScroll("/?q=wolfstar")
    expect(sessionStorage.getItem(KEY)).toContain("1400")
  })
})
