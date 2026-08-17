import { describe, it, expect } from "vitest"
import { encodeStoryId, decodeStoryId, shortStoryPath } from "./shortId"

// The whole value of this scheme is that it needs no storage, which means the
// only thing that can go wrong is the arithmetic. A code that decodes to the
// WRONG uuid is the dangerous failure — it sends a reader to a real page for a
// work they did not ask for — so the round trip and the rejection of anything
// malformed are what these assert.

const UUID = "4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c"

describe("encodeStoryId", () => {
  it("is 22 characters for a uuid", () => {
    expect(encodeStoryId(UUID)).toHaveLength(22)
  })

  it("is shorter than the uuid it replaces", () => {
    expect(encodeStoryId(UUID)!.length).toBeLessThan(UUID.length)
  })

  it("uses only url-safe characters — no +, / or = to escape", () => {
    expect(encodeStoryId(UUID)).toMatch(/^[A-Za-z0-9_-]+$/)
  })

  it("is case-preserving and stable", () => {
    expect(encodeStoryId(UUID)).toBe(encodeStoryId(UUID))
    expect(encodeStoryId(UUID.toUpperCase())).toBe(encodeStoryId(UUID))
  })

  it("rejects anything that is not a uuid", () => {
    expect(encodeStoryId("not-a-uuid")).toBeNull()
    expect(encodeStoryId("")).toBeNull()
    expect(encodeStoryId("4b15fe7e51aa46c6b8ecf0738c8e7b3c")).toBeNull()
  })
})

describe("decodeStoryId", () => {
  it("round-trips", () => {
    expect(decodeStoryId(encodeStoryId(UUID)!)).toBe(UUID)
  })

  it("round-trips uuids with high bytes and zero bytes", () => {
    for (const u of [
      "00000000-0000-0000-0000-000000000000",
      "ffffffff-ffff-ffff-ffff-ffffffffffff",
      "ff00ff00-00ff-00ff-ff00-00ff00ff00ff",
      "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    ]) {
      expect(decodeStoryId(encodeStoryId(u)!)).toBe(u)
    }
  })

  it("rejects a code of the wrong length rather than guessing", () => {
    // 22 base64url chars is exactly 16 bytes. Anything else is not one of ours,
    // and decoding it anyway would yield a plausible uuid for the wrong work.
    expect(decodeStoryId("tooshort")).toBeNull()
    expect(decodeStoryId("A".repeat(21))).toBeNull()
    expect(decodeStoryId("A".repeat(23))).toBeNull()
    expect(decodeStoryId("")).toBeNull()
  })

  it("rejects characters outside the alphabet", () => {
    expect(decodeStoryId("A".repeat(21) + "!")).toBeNull()
    expect(decodeStoryId("A".repeat(21) + "/")).toBeNull()
    expect(decodeStoryId("A".repeat(21) + "+")).toBeNull()
  })

  it("never returns two uuids for one code", () => {
    const seen = new Map<string, string>()
    for (let i = 0; i < 200; i++) {
      const u = [
        i.toString(16).padStart(8, "0"), "1111", "2222", "3333",
        (i * 7).toString(16).padStart(12, "0"),
      ].join("-")
      const code = encodeStoryId(u)!
      expect(seen.has(code)).toBe(false)
      seen.set(code, u)
      expect(decodeStoryId(code)).toBe(u)
    }
  })
})

describe("shortStoryPath", () => {
  it("shortens the path a reader would copy", () => {
    const short = shortStoryPath(UUID)
    expect(short).toBe(`/s/${encodeStoryId(UUID)}`)
    expect(short.length).toBeLessThan(`/story/${UUID}`.length)
  })

  it("falls back to the canonical path rather than emitting a broken link", () => {
    expect(shortStoryPath("nonsense")).toBe("/story/nonsense")
  })
})
