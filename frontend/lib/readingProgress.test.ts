import { describe, it, expect } from "vitest"
import { resumePoint } from "./readingProgress"
import { readFileSync } from "node:fs"
import path from "node:path"

describe("resumePoint", () => {
  it("a stale tab re-saving an old chapter does not move the reader back", () => {
    // The real record: positions through 61, `chapter` overwritten to 22.
    const positions: Record<string, number> = {}
    for (let i = 1; i <= 60; i++) positions[i] = 0.99
    positions[61] = 0.03
    const r = resumePoint({ chapter: 22, positions, scrollPct: 0.99 })!
    expect(r.chapter).toBe(61)
    expect(r.pct).toBeCloseTo(0.03)
    expect(r.lastOpened).toBe(22)
  })
  it("ordinary forward reading resumes where it was", () => {
    const r = resumePoint({ chapter: 5, positions: { 4: 1, 5: 0.4 } })!
    expect(r).toEqual({ chapter: 5, pct: 0.4, lastOpened: null })
  })
  it("a chapter opened but not yet scrolled still counts", () => {
    expect(resumePoint({ chapter: 6, positions: { 5: 1 } })!.chapter).toBe(6)
  })
  it("no progress, no resume", () => {
    expect(resumePoint({})).toBeNull()
    expect(resumePoint(null)).toBeNull()
  })
})

describe("the reader only saves what the reader did", () => {
  const src = readFileSync(path.join(__dirname, "../app/story/[id]/chapter/[num]/page.tsx"), "utf8")
  it("a visibility change flushes a pending save, never an unconditional one", () => {
    // An unconditional save on visibilitychange let a stale tab re-assert an
    // old chapter with a fresh timestamp just by being shown.
    const flush = src.match(/const flush = \(\) => \{[^\n]*\}/)?.[0] ?? ""
    expect(flush).toMatch(/if \(timer\)\s*\{[^}]*save\(\)\s*\}/)
  })
})
