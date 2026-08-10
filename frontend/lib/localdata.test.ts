import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  DATA_GROUPS, clearGroup, exportAll, groupSize, type DataGroup,
} from "./localdata"

// Controls for the data this site keeps on your device.
//
// Two failures matter here and neither is visible from the outside:
//
//   * a Clear that clears the wrong thing. These groups are not recoverable —
//     "Reading progress" is your place in every story you are part-way through —
//     and clearing an adjacent group instead would be silent until someone went
//     looking for something that had gone.
//   * an export that quietly omits data. The whole point of it is that a reader
//     does not have to take our summary of what we hold on trust, so a missing
//     key makes the feature worse than not offering it.

const group = (id: string): DataGroup => {
  const g = DATA_GROUPS.find(x => x.id === id)
  if (!g) throw new Error(`no such group: ${id}`)
  return g
}

beforeEach(() => {
  localStorage.clear()
})

describe("DATA_GROUPS", () => {
  it("claims no key twice", () => {
    // Overlap would make one group's Clear delete another's data, which is the
    // exact failure the group boundaries exist to prevent.
    const all = DATA_GROUPS.flatMap(g => g.keys)
    expect(new Set(all).size).toBe(all.length)
  })

  it("only ever touches this site's own keys", () => {
    for (const key of DATA_GROUPS.flatMap(g => g.keys)) {
      expect(key.startsWith("ficatlas:")).toBe(true)
    }
  })

  it("gives every group a stable id and a description", () => {
    const ids = DATA_GROUPS.map(g => g.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const g of DATA_GROUPS) {
      expect(g.name.length).toBeGreaterThan(0)
      expect(g.hint.length).toBeGreaterThan(0)
      expect(g.keys.length).toBeGreaterThan(0)
    }
  })

  it("separates reading progress from reader preferences", () => {
    // They are both "reader" data internally and are completely different things
    // to lose: one is a setting, the other is where you had got to.
    const progress = group("progress").keys
    const prefs = group("prefs").keys
    expect(progress).toContain("ficatlas:progress")
    expect(prefs).toContain("ficatlas:reader_theme")
    expect(progress.some(k => prefs.includes(k))).toBe(false)
  })
})

describe("groupSize", () => {
  it("is zero when nothing is stored", () => {
    expect(groupSize(group("history"))).toBe(0)
  })

  it("grows once data exists", () => {
    localStorage.setItem("ficatlas:recent-searches", JSON.stringify(["harry potter"]))
    expect(groupSize(group("history"))).toBeGreaterThan(0)
  })

  it("counts only its own group's keys", () => {
    localStorage.setItem("ficatlas:progress", JSON.stringify({ a: 1 }))
    expect(groupSize(group("history"))).toBe(0)
    expect(groupSize(group("progress"))).toBeGreaterThan(0)
  })

  it("ignores keys that are absent rather than counting them as empty strings", () => {
    localStorage.setItem("ficatlas:recent-searches", "[]")
    const withOne = groupSize(group("history"))
    localStorage.setItem("ficatlas:recents", "[]")
    expect(groupSize(group("history"))).toBeGreaterThan(withOne)
  })
})

describe("clearGroup", () => {
  it("removes every key in the group", () => {
    for (const k of group("history").keys) localStorage.setItem(k, "x")
    clearGroup(group("history"))
    for (const k of group("history").keys) expect(localStorage.getItem(k)).toBeNull()
  })

  it("leaves other groups untouched", () => {
    localStorage.setItem("ficatlas:recent-searches", "x")
    localStorage.setItem("ficatlas:progress", "keep me")
    localStorage.setItem("ficatlas:bookmarks", "keep me too")
    clearGroup(group("history"))
    expect(localStorage.getItem("ficatlas:progress")).toBe("keep me")
    expect(localStorage.getItem("ficatlas:bookmarks")).toBe("keep me too")
  })

  it("leaves anything that is not ours untouched", () => {
    localStorage.setItem("someone-elses-key", "not ours")
    for (const g of DATA_GROUPS) clearGroup(g)
    expect(localStorage.getItem("someone-elses-key")).toBe("not ours")
  })

  it("is safe on an already-empty group", () => {
    expect(() => clearGroup(group("history"))).not.toThrow()
  })

  it("does not throw when storage refuses the removal", () => {
    const spy = vi.spyOn(Storage.prototype, "removeItem")
      .mockImplementation(() => { throw new Error("SecurityError") })
    expect(() => clearGroup(group("history"))).not.toThrow()
    spy.mockRestore()
  })
})

describe("exportAll", () => {
  it("produces valid JSON with a timestamp", () => {
    const parsed = JSON.parse(exportAll())
    expect(typeof parsed.exported_at).toBe("string")
    expect(Number.isNaN(Date.parse(parsed.exported_at))).toBe(false)
  })

  it("includes every ficatlas key, not just the grouped ones", () => {
    // The file answers "what do you have on me?", so it must not be limited to
    // the categories the settings page happens to list.
    localStorage.setItem("ficatlas:recent-searches", JSON.stringify(["a"]))
    localStorage.setItem("ficatlas:something-new", JSON.stringify({ x: 1 }))
    const data = JSON.parse(exportAll()).data
    expect(data["ficatlas:recent-searches"]).toEqual(["a"])
    expect(data["ficatlas:something-new"]).toEqual({ x: 1 })
  })

  it("excludes keys belonging to other sites", () => {
    localStorage.setItem("unrelated", "secret")
    const data = JSON.parse(exportAll()).data
    expect(Object.keys(data)).not.toContain("unrelated")
  })

  it("unpacks JSON values rather than exporting escaped strings", () => {
    localStorage.setItem("ficatlas:bookmarks", JSON.stringify([{ id: "abc" }]))
    const data = JSON.parse(exportAll()).data
    expect(data["ficatlas:bookmarks"]).toEqual([{ id: "abc" }])
  })

  it("keeps a non-JSON value as its raw string", () => {
    localStorage.setItem("ficatlas:theme", "dark")
    expect(JSON.parse(exportAll()).data["ficatlas:theme"]).toBe("dark")
  })

  it("works when there is nothing stored", () => {
    const parsed = JSON.parse(exportAll())
    expect(parsed.data).toEqual({})
  })

  it("says that offline works are not included", () => {
    // They live in IndexedDB and are megabytes; claiming completeness without
    // them would be the misleading kind of true.
    expect(JSON.parse(exportAll()).note).toMatch(/IndexedDB/)
  })
})
