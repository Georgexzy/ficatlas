import { describe, it, expect, beforeEach } from "vitest"
import {
  loadSaved, saveSearch, removeSaved, isSaved, noteRun, newSince, searchId,
  MAX_SAVED, type SavedSearch,
} from "./savedSearches"

const KEY = "ficatlas:saved-searches"

describe("saved searches", () => {
  beforeEach(() => localStorage.clear())

  it("saves a query and finds it again", () => {
    saveSearch('fandom:"Harry Potter" complete')
    expect(isSaved('fandom:"Harry Potter" complete')).toBe(true)
    expect(loadSaved()).toHaveLength(1)
  })

  it("treats spacing and case as the same search", () => {
    // Saving from the bar and saving the same thing after a filter click must not
    // produce two entries that both have to be deleted later.
    saveSearch("drarry   complete")
    saveSearch("Drarry complete")
    expect(loadSaved()).toHaveLength(1)
  })

  it("keeps the reader's own spelling, and dedups on the normalised form", () => {
    saveSearch("Drarry Complete")
    expect(loadSaved()[0].q).toBe("Drarry Complete")
    expect(loadSaved()[0].id).toBe("drarry complete")
  })

  it("moves a re-saved search to the top rather than duplicating it", () => {
    saveSearch("a")
    saveSearch("b")
    saveSearch("a")
    expect(loadSaved().map(s => s.q)).toEqual(["a", "b"])
  })

  it("refuses an empty query", () => {
    saveSearch("   ")
    expect(loadSaved()).toHaveLength(0)
  })

  it("removes by id", () => {
    saveSearch("keep me")
    saveSearch("drop me")
    removeSaved(searchId("drop me"))
    expect(loadSaved().map(s => s.q)).toEqual(["keep me"])
  })

  it("caps the list", () => {
    for (let i = 0; i < MAX_SAVED + 10; i++) saveSearch(`q${i}`)
    expect(loadSaved()).toHaveLength(MAX_SAVED)
    // Newest kept, so saving one more never silently drops the one just saved.
    expect(loadSaved()[0].q).toBe(`q${MAX_SAVED + 9}`)
  })

  it("survives corrupt storage", () => {
    localStorage.setItem(KEY, "{not json")
    expect(loadSaved()).toEqual([])
  })

  it("drops entries with no query rather than rendering a blank row", () => {
    localStorage.setItem(KEY, JSON.stringify([{ id: "x" }, { q: "real" }]))
    expect(loadSaved().map(s => s.q)).toEqual(["real"])
  })

  it("fills in a missing id from the query", () => {
    // An entry written by an older version, or merged from a server that had one.
    localStorage.setItem(KEY, JSON.stringify([{ q: "Time Travel" }]))
    expect(loadSaved()[0].id).toBe("time travel")
  })

  describe("what is new since last time", () => {
    const entry = (over: Partial<SavedSearch> = {}): SavedSearch =>
      ({ id: "q", q: "q", at: "2026-01-01T00:00:00Z", ...over })

    it("says nothing until the search has been run once", () => {
      expect(newSince(entry(), 500)).toBeNull()
    })

    it("counts the gain", () => {
      expect(newSince(entry({ last_total: 100 }), 104)).toBe(4)
    })

    it("says nothing when the count has not moved", () => {
      expect(newSince(entry({ last_total: 100 }), 100)).toBeNull()
    })

    it("says nothing when the result set SHRANK", () => {
      // A result set can shrink — a work delisted, an author opting out, a gate
      // list getting stricter — and "-3 new" is not a thing to show anybody.
      // Same clamp as new_chapters in api/follows.py.
      expect(newSince(entry({ last_total: 100 }), 97)).toBeNull()
    })

    it("records a total when the saved search is run", () => {
      saveSearch("drarry")
      noteRun("drarry", 1186)
      expect(loadSaved()[0].last_total).toBe(1186)
      expect(loadSaved()[0].last_run).toBeTruthy()
    })

    it("ignores a run of a search nobody saved", () => {
      // The common case by far, and it must not create an entry: a reader who
      // has saved nothing should not accumulate a list by searching.
      noteRun("some passing query", 12)
      expect(loadSaved()).toHaveLength(0)
    })

    it("records the total at save time when it is known", () => {
      // Saving a search you are looking at should not start blind — otherwise the
      // first re-run has nothing to compare against and says nothing.
      saveSearch("drarry", 1186)
      expect(loadSaved()[0].last_total).toBe(1186)
    })
  })
})
