import { describe, it, expect } from "vitest"
import { parseFandomSlugs, joinFandomSlugs, MAX_YOUR_FANDOMS } from "./yourFandoms"

// The list crosses the wire as the comma-joined string signup() wrote and as
// the array a checkbox picker holds. These pin the two directions against each
// other, because the failure is silent: a round trip that drops an entry shows
// a reader a home page missing a fandom they can no longer see anywhere.

describe("parseFandomSlugs", () => {
  it("reads the stored string in the reader's own order", () => {
    expect(parseFandomSlugs("harry-potter,star-wars,pokemon")).toEqual([
      "harry-potter", "star-wars", "pokemon",
    ])
  })

  it("drops blanks and collapses duplicates, keeping the first spelling", () => {
    expect(parseFandomSlugs(" harry-potter , ,star-wars,harry-potter "))
      .toEqual(["harry-potter", "star-wars"])
  })

  it("accepts an array, so a client that writes one loses nothing", () => {
    expect(parseFandomSlugs(["star-wars", "pokemon"])).toEqual(["star-wars", "pokemon"])
  })

  it("refuses to throw at a value that is not a list at all", () => {
    // This reads a row out of an account. Anything unexpected has to read as
    // "chose nothing" rather than taking the page down with it.
    expect(parseFandomSlugs(null)).toEqual([])
    expect(parseFandomSlugs(undefined)).toEqual([])
    expect(parseFandomSlugs(42)).toEqual([])
    expect(parseFandomSlugs({ slug: "harry-potter" })).toEqual([])
  })

  it("caps the list where the server does", () => {
    const many = Array.from({ length: 60 }, (_, i) => `fandom-${i}`).join(",")
    expect(parseFandomSlugs(many)).toHaveLength(MAX_YOUR_FANDOMS)
  })
})

describe("joinFandomSlugs", () => {
  it("is exactly what parse undoes", () => {
    const slugs = ["harry-potter", "star-wars", "pokemon"]
    expect(parseFandomSlugs(joinFandomSlugs(slugs))).toEqual(slugs)
  })

  it("does not write the whitespace back into the account", () => {
    expect(joinFandomSlugs([" harry-potter ", "", "star-wars"]))
      .toBe("harry-potter,star-wars")
  })

  it("is empty for nothing, not a stray separator", () => {
    // "a,,b" stored as "," would parse as one empty entry forever after.
    expect(joinFandomSlugs([])).toBe("")
    expect(parseFandomSlugs("")).toEqual([])
  })
})
