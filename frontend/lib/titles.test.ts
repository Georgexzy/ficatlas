import { describe, it, expect } from "vitest"
import { looksTruncated, displayTitle } from "./api"

// The AO3 dump cut ~306,000 titles mid-phrase. These are marked rather than
// hidden, and the test that matters is the one that keeps the marking narrow:
// a false positive libels a real title as damaged, on a site where the title is
// the author's.
describe("looksTruncated", () => {
  it("marks titles cut after a joining word", () => {
    for (const t of [
      "One Foot in Front of the",
      "Ill Be Your Light In The",
      "BRING ME THE HEAD OF THE",
      "The Phoenix The Sun and",
      "Mixing Pleasure with",
      "A Certain Kind of",
    ]) expect(looksTruncated(t), t).toBe(true)
  })

  it("leaves real titles alone", () => {
    // Every one of these is a genuine title in the index. They end in words the
    // repair queue deliberately does NOT treat as truncation, because titles
    // ending in "to", "in", "her" or "me" are ordinary English.
    for (const t of [
      "a pain that i'm used to",
      "that's how the light gets in",
      "Nothing Left to Hold On to",
      "loving her",
      "just ask me to",
      "I know you think of me when you think of her",
      "And the Award Goes To…",
      "Previously on…",
    ]) expect(looksTruncated(t), t).toBe(false)
  })

  it("handles empty and missing titles", () => {
    expect(looksTruncated("")).toBe(false)
    expect(looksTruncated(null)).toBe(false)
    expect(looksTruncated(undefined)).toBe(false)
    expect(displayTitle(null)).toBe("")
  })

  it("appends an ellipsis only to the truncated ones", () => {
    expect(displayTitle("A Certain Kind of")).toBe("A Certain Kind of…")
    expect(displayTitle("loving her")).toBe("loving her")
    // Already ends in an ellipsis of its own — must not gain a second.
    expect(displayTitle("Previously on…")).toBe("Previously on…")
  })

  it("is case-insensitive and tolerates surrounding space", () => {
    expect(displayTitle("  Something AND  ")).toBe("Something AND…")
  })
})
