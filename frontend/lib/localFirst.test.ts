import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { localFirst, DEFAULT_PATIENCE_MS } from "./localFirst"

/** A promise that never settles — a dead-but-associated connection, which is
 *  what fetch() actually does there. It does not reject; it hangs. */
const hangs = <T,>(signal?: AbortSignal) => new Promise<T>((_res, rej) => {
  signal?.addEventListener("abort", () => rej(new Error("aborted")))
})

const collect = () => {
  const events: string[] = []
  return {
    events,
    onLocal:   (v: any) => events.push(`local:${v}`),
    onRemote:  (v: any) => events.push(`remote:${v}`),
    onFailure: (e: any) => events.push(`fail:${(e as Error)?.message ?? e}`),
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe("the reported failure", () => {
  it("shows a saved chapter IMMEDIATELY when the network hangs", async () => {
    // This is the bug, reduced. Measured before the change: 21 seconds, because
    // the saved copy was only reached after the network's abort timer fired.
    const c = collect()
    const run = localFirst({
      local: async () => "saved chapter",
      remote: (s) => hangs(s),
      ...c,
    })
    // Not one timer has advanced.
    await Promise.resolve(); await Promise.resolve()
    expect(c.events).toEqual(["local:saved chapter"])

    // And the hanging network never produces a failure over text on screen.
    await vi.advanceTimersByTimeAsync(60_000)
    await run
    expect(c.events).toEqual(["local:saved chapter"])
  })

  it("does not consult navigator.onLine", async () => {
    // The old code gated the local read on `!navigator.onLine`, which is `true`
    // on a dead-but-associated connection — so the gate never opened in the one
    // case it existed for. Nothing here may depend on that flag.
    const src = localFirst.toString()
    expect(src).not.toContain("onLine")
  })
})

describe("local first, network as an upgrade", () => {
  it("renders local then replaces it when the network answers", async () => {
    const c = collect()
    await localFirst({ local: async () => "stale", remote: async () => "fresh", ...c })
    expect(c.events).toEqual(["local:stale", "remote:fresh"])
  })

  it("goes to the network alone when nothing is held", async () => {
    const c = collect()
    await localFirst({ local: async () => null, remote: async () => "fresh", ...c })
    expect(c.events).toEqual(["remote:fresh"])
  })

  it("reports failure ONLY when there is nothing on screen", async () => {
    const withCopy = collect()
    await localFirst({ local: async () => "saved", remote: async () => { throw new Error("502") }, ...withCopy })
    expect(withCopy.events).toEqual(["local:saved"])

    const without = collect()
    await localFirst({ local: async () => null, remote: async () => { throw new Error("502") }, ...without })
    expect(without.events).toEqual(["fail:502"])
  })

  it("treats a broken local store as 'nothing held', not as a failure", async () => {
    // A quota eviction or a corrupt object store must not also cost the reader
    // the network copy.
    const c = collect()
    await localFirst({
      local: async () => { throw new Error("IDB gone") },
      remote: async () => "fresh", ...c,
    })
    expect(c.events).toEqual(["remote:fresh"])
  })
})

describe("the timeouts bound the network, never the reader", () => {
  it("gives a reader with nothing the patience budget, then fails", async () => {
    const c = collect()
    const run = localFirst({ local: async () => null, remote: (s) => hangs(s),
                             patienceMs: 8_000, ...c })
    await vi.advanceTimersByTimeAsync(7_000)
    expect(c.events).toEqual([])
    await vi.advanceTimersByTimeAsync(2_000)
    await run
    expect(c.events).toEqual(["fail:aborted"])
  })

  it("defaults the patience budget to something a person will actually wait", () => {
    // Measured: a chapter costs 12-54ms locally and 130-510ms through the edge.
    // A budget in the tens of seconds is spent entirely on a connection that has
    // already failed, and the reader sits in front of a spinner for all of it.
    expect(DEFAULT_PATIENCE_MS).toBeLessThanOrEqual(10_000)
    expect(DEFAULT_PATIENCE_MS).toBeGreaterThan(3_000)
  })

  it("never makes a reader who HAS the text wait for any budget", async () => {
    const c = collect()
    const run = localFirst({ local: async () => "saved", remote: (s) => hangs(s),
                             patienceMs: 20_000, graceMs: 30_000, ...c })
    await Promise.resolve(); await Promise.resolve()
    // Rendered before any clock moved at all — that is the whole property.
    expect(c.events).toEqual(["local:saved"])
    await vi.advanceTimersByTimeAsync(40_000)
    await run
  })
})

describe("cancellation", () => {
  it("does not render into a page the reader has left", async () => {
    // A slow response for a chapter already navigated away from must not
    // replace the one being read.
    const c = collect()
    let gone = false
    const run = localFirst({
      local: async () => null,
      remote: async () => { gone = true; return "late" },
      isCancelled: () => gone, ...c,
    })
    await run
    expect(c.events).toEqual([])
  })

  it("does not render a local copy after cancellation either", async () => {
    const c = collect()
    await localFirst({
      local: async () => "saved", remote: async () => "fresh",
      isCancelled: () => true, ...c,
    })
    expect(c.events).toEqual([])
  })
})
