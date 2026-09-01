import { describe, it, expect } from "vitest"
import { seriesNote } from "./seriesNote"

const w = (...positions: (number | null)[]) => positions.map(position => ({ position }))

describe("seriesNote", () => {
  it("says nothing when the series is whole", () => {
    expect(seriesNote("explicit", w(1, 2, 3))).toBeNull()
  })

  it("reports works missing before the first one held", () => {
    const n = seriesNote("explicit", w(7, 8, 9))!
    expect(n.missingBefore).toBe(true)
    expect(n.lowest).toBe(7)
    expect(n.gaps).toBe(false)
  })

  it("reports a hole inside the run", () => {
    expect(seriesNote("explicit", w(1, 2, 4))!.gaps).toBe(true)
  })

  describe("what it may claim", () => {
    it("attributes an archive series' numbering to the author", () => {
      const n = seriesNote("explicit", w(2, 3))!
      expect(n.whose).toContain("the author's own")
      expect(n.canPointAtArchive).toBe(true)
    })

    it("does not promise the archive holds the rest of a stated series", () => {
      // The grouping was read out of summaries — "third in the…". The works it
      // names may be anywhere, or nowhere.
      const n = seriesNote("stated", w(2, 3))!
      expect(n.whose).toContain("what they wrote in their summaries")
      expect(n.canPointAtArchive).toBe(false)
    })

    it.each(["inferred", "sequel", "summary"])(
      "says nothing at all for a %s grouping", source => {
        // These are FicAtlas's own publication order. A gap is our parser
        // missing a cue, and claiming the author numbered it contradicts the
        // provenance paragraph directly above.
        expect(seriesNote(source, w(7, 8, 9))).toBeNull()
        expect(seriesNote(source, w(1, 2, 4))).toBeNull()
      })
  })

  describe("arithmetic that misfired", () => {
    it("does not invent a gap from a duplicate position", () => {
      // series_works is unique on (series_id, story_id), not on position, so
      // two works can share one. max-min+1 !== length then fires backwards.
      expect(seriesNote("explicit", w(1, 2, 2, 3))).toBeNull()
    })

    it("counts companions, which also carry positions", () => {
      // Filtering to the main run produced 1,2,4 for a complete series whose
      // third work is a companion, and reported a gap above the companion.
      expect(seriesNote("explicit", w(1, 2, 3, 4))).toBeNull()
    })

    it("ignores works with no position at all", () => {
      expect(seriesNote("explicit", w(1, null, 2))).toBeNull()
    })

    it("says nothing when no work has a position", () => {
      expect(seriesNote("explicit", w(null, null))).toBeNull()
    })
  })
})
