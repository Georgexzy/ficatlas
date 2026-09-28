import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { fetchWithTimeout, fetchBackground, BACKGROUND_TIMEOUT_MS } from "./net"

/** A fetch that hangs until aborted — what a dead-but-associated connection
 *  actually does. It does not reject on its own. */
function hangingFetch() {
  const started: AbortSignal[] = []
  const abortErr = () => Object.assign(new Error("aborted"), { name: "AbortError" })
  const fn = vi.fn((_input: any, init: any = {}) => new Promise<Response>((_res, rej) => {
    started.push(init.signal)
    // Mimic the platform: fetch() rejects at once for a signal that is ALREADY
    // aborted, rather than waiting for an "abort" event that has been and gone.
    if (init.signal?.aborted) return rej(abortErr())
    init.signal?.addEventListener("abort", () => rej(abortErr()))
  }))
  return { fn, started }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe("a timeout must free the socket, not just stop waiting", () => {
  it("ABORTS the request on timeout", async () => {
    const { fn, started } = hangingFetch()
    vi.stubGlobal("fetch", fn)
    const p = fetchWithTimeout("/api/x", {}, 5_000).catch(e => e.name)
    await vi.advanceTimersByTimeAsync(4_000)
    expect(started[0].aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(2_000)
    // The socket is released. A version that merely cleared a timer and moved
    // on would leave this false, and six of those wedge the whole origin.
    expect(started[0].aborted).toBe(true)
    expect(await p).toBe("AbortError")
  })

  it("honours a caller's own signal as well as the timeout", async () => {
    const { fn, started } = hangingFetch()
    vi.stubGlobal("fetch", fn)
    const outer = new AbortController()
    const p = fetchWithTimeout("/api/x", { signal: outer.signal }, 60_000).catch(e => e.name)
    outer.abort()
    await vi.advanceTimersByTimeAsync(0)
    expect(started[0].aborted).toBe(true)
    expect(await p).toBe("AbortError")
  })

  it("aborts immediately when the caller's signal is already aborted", async () => {
    const { fn } = hangingFetch()
    vi.stubGlobal("fetch", fn)
    const outer = new AbortController()
    outer.abort()
    const p = fetchWithTimeout("/api/x", { signal: outer.signal }, 60_000).catch(e => e.name)
    await vi.advanceTimersByTimeAsync(0)
    expect(await p).toBe("AbortError")
  })

  it("clears its timer when the request succeeds, so nothing fires later", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("ok")))
    const r = await fetchWithTimeout("/api/x", {}, 5_000)
    expect(r.ok).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe("background traffic", () => {
  it("never rejects, and still releases the socket", async () => {
    const { fn, started } = hangingFetch()
    vi.stubGlobal("fetch", fn)
    // No unhandled rejection, no throw.
    fetchBackground("/api/traffic/hit", { method: "POST" })
    await vi.advanceTimersByTimeAsync(BACKGROUND_TIMEOUT_MS + 1_000)
    expect(started[0].aborted).toBe(true)
  })

  it("gives background traffic a short leash", () => {
    // Nobody is waiting on a pageview beacon; it must not hold a connection
    // slot that a chapter needs.
    expect(BACKGROUND_TIMEOUT_MS).toBeLessThanOrEqual(5_000)
  })
})
