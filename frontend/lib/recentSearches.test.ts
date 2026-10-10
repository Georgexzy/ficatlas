import { beforeEach, describe, expect, it } from "vitest"
import {
  clearRecentSearches, loadRecentSearches, rememberRecentSearch,
  removeRecentSearch,
} from "./recentSearches"

const KEY = "ficatlas:recent-searches"

describe("recent searches", () => {
  beforeEach(() => localStorage.clear())

  it("removes one search and preserves the remaining order", () => {
    localStorage.setItem(KEY, JSON.stringify(["first", "remove me", "last"]))

    expect(removeRecentSearch("remove me")).toEqual(["first", "last"])
    expect(loadRecentSearches()).toEqual(["first", "last"])
  })

  it("removes all exact duplicates of a dismissed search", () => {
    localStorage.setItem(KEY, JSON.stringify(["keep", "drop", "drop"]))

    expect(removeRecentSearch("drop")).toEqual(["keep"])
  })

  it("moves a repeated search to the top without duplicating it", () => {
    localStorage.setItem(KEY, JSON.stringify(["older", "newer"]))

    expect(rememberRecentSearch(" older ")).toEqual(["older", "newer"])
  })

  it("clears the history", () => {
    rememberRecentSearch("keep")
    clearRecentSearches()

    expect(loadRecentSearches()).toEqual([])
  })

  it("recovers from malformed storage", () => {
    localStorage.setItem(KEY, "not json")

    expect(loadRecentSearches()).toEqual([])
  })
})
