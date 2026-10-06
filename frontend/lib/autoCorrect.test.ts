import { describe, it, expect } from "vitest"
import { confidentSpelling, editDistance, fixOperatorTypos } from "./autoCorrect"

describe("autoCorrect", () => {
  it("measures edits", () => {
    expect(editDistance("arithmancr", "arithmancer")).toBe(1)
    expect(editDistance("shgip", "ship")).toBe(1)
  })

  it("fixes a mistyped operator and leaves real ones alone", () => {
    expect(fixOperatorTypos('shgip:"Regulus Black/James Potter" tag:"Marauders"'))
      .toBe('ship:"Regulus Black/James Potter" tag:"Marauders"')
    expect(fixOperatorTypos("fandmo:Naruto")).toBe("fandom:Naruto")
    expect(fixOperatorTypos("fandom:Naruto tag:Fluff")).toBeNull()
    // A colon in prose is not an operator: nothing near "chapter".
    expect(fixOperatorTypos("chapter 3: the return")).toBeNull()
    expect(fixOperatorTypos("Naruto: time travel")).toBeNull()
  })

  it("corrects a typo the site already knows the answer to", () => {
    const s = [{ reason: "spelling", value: "The Arithmancer", query: "The Arithmancer", count: 4461, kind: "title" }]
    expect(confidentSpelling("the arithmancr", s)).toBe("The Arithmancer")
  })

  it("does not swap a search for a different one", () => {
    // Real cases: the suggestion was a related tag, not the same words.
    expect(confidentSpelling("catflap to freedom",
      [{ reason: "spelling", value: "Freedom", query: "Freedom", count: 1867 }])).toBeNull()
    expect(confidentSpelling("Twilight jacobxoc smut",
      [{ reason: "spelling", value: "Twilight", query: "fandom:Twilight", count: 231288 }])).toBeNull()
  })

  it("never echoes the search back, and never rewrites operator searches", () => {
    expect(confidentSpelling("King_of_WOW",
      [{ reason: "spelling", value: "King_of_WOW", query: "author:King_of_WOW", count: 77 }])).toBeNull()
    expect(confidentSpelling("author:King_of_WOW",
      [{ reason: "spelling", value: "King_of_WOW", query: "author:King_of_WOW", count: 77 }])).toBeNull()
  })
})
