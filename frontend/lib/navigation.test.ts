import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { navigateTo, ROUTER_PATIENCE_MS, OFFLINE_ROUTER_PATIENCE_MS } from "./navigation"

/** Stands in for the browser: a location whose pathname the fake router may or
 *  may not manage to change, and an assignable href we can watch. */
function browser(startPath = "/story/x/chapter/1", onLine = true) {
  const loc = {
    pathname: startPath,
    _href: "",
    get href() { return this._href },
    set href(v: string) { this._href = v; this.pathname = v.split(/[?#]/)[0] },
  }
  vi.stubGlobal("window", { location: loc, setTimeout: globalThis.setTimeout })
  vi.stubGlobal("navigator", { onLine })
  return loc
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe("the client router gets a chance, then the document request does", () => {
  it("does nothing more when the router actually navigates", async () => {
    const loc = browser()
    navigateTo((h) => { loc.pathname = h.split(/[?#]/)[0] }, "/story/x/chapter/2")
    await vi.advanceTimersByTimeAsync(ROUTER_PATIENCE_MS + 100)
    // No hard navigation was needed.
    expect(loc._href).toBe("")
    expect(loc.pathname).toBe("/story/x/chapter/2")
  })

  it("falls back to a document request when the router silently fails", async () => {
    const loc = browser()
    navigateTo(() => { /* the RSC fetch never resolves; nothing changes */ },
               "/story/x/chapter/2")
    await vi.advanceTimersByTimeAsync(ROUTER_PATIENCE_MS + 100)
    expect(loc._href).toBe("/story/x/chapter/2")
  })

  it("keeps a query string the router legitimately added", async () => {
    const loc = browser()
    navigateTo((h) => { loc.pathname = h.split(/[?#]/)[0] }, "/library?tab=shelf")
    await vi.advanceTimersByTimeAsync(ROUTER_PATIENCE_MS + 100)
    expect(loc._href).toBe("")
  })
})

describe("how long the reader waits before the fallback", () => {
  it("waits the short time when navigator.onLine says offline", async () => {
    const loc = browser("/a", false)
    navigateTo(() => {}, "/story/x/chapter/2")
    await vi.advanceTimersByTimeAsync(OFFLINE_ROUTER_PATIENCE_MS + 50)
    expect(loc._href).toBe("/story/x/chapter/2")
  })

  it("waits the short time when the CALLER saw the network fail", async () => {
    // The case navigator.onLine cannot see: dead but associated, so it reports
    // `true` and every page turn paid the full patience for an RSC payload that
    // was never coming. A caller that just watched its own fetch fail knows
    // better, and saves the reader that wait on every single page.
    const loc = browser("/a", true)
    navigateTo(() => {}, "/story/x/chapter/2", { networkAlive: false })
    await vi.advanceTimersByTimeAsync(OFFLINE_ROUTER_PATIENCE_MS + 50)
    expect(loc._href).toBe("/story/x/chapter/2")
  })

  it("still gives a working network the full patience", async () => {
    const loc = browser("/a", true)
    navigateTo(() => {}, "/story/x/chapter/2", { networkAlive: true })
    await vi.advanceTimersByTimeAsync(OFFLINE_ROUTER_PATIENCE_MS + 50)
    expect(loc._href).toBe("")           // not yet
    await vi.advanceTimersByTimeAsync(ROUTER_PATIENCE_MS)
    expect(loc._href).toBe("/story/x/chapter/2")
  })

  it("never SKIPS the router, only shortens the wait", async () => {
    // A route already in the router cache needs no network and renders
    // instantly; skipping straight to a document request offline would reboot
    // the whole app for every chapter, which is what "ages to go to the next
    // chapter" actually was.
    const loc = browser("/a", false)
    const push = vi.fn((h: string) => { loc.pathname = h.split(/[?#]/)[0] })
    navigateTo(push, "/story/x/chapter/2", { networkAlive: false })
    expect(push).toHaveBeenCalledWith("/story/x/chapter/2")
    await vi.advanceTimersByTimeAsync(ROUTER_PATIENCE_MS + 100)
    expect(loc._href).toBe("")
  })
})
