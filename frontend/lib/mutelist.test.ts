import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  EMPTY_MUTES, MUTES_CHANGED, loadMutes, muteCount, saveMutes, withMutes,
  type MuteList,
} from "./mutelist"

// The standing "never show me" list.
//
// The failure that matters is failing OPEN: a mute list that silently stops
// applying shows someone exactly the thing they asked never to see, and does it
// quietly. Everything here is written from that direction — corrupt storage,
// missing fields and unavailable localStorage must degrade to "show everything"
// only where there is genuinely nothing to honour, and must never drop a mute
// that was saved successfully.

beforeEach(() => {
  localStorage.clear()
})

const list = (over: Partial<MuteList> = {}): MuteList => ({ ...EMPTY_MUTES, ...over })

describe("loadMutes", () => {
  it("returns an empty list when nothing is stored", () => {
    expect(loadMutes()).toEqual(EMPTY_MUTES)
  })

  it("round-trips a saved list", () => {
    const m = list({ tags: ["Character Death"], authors: ["SomeAuthor"] })
    saveMutes(m)
    expect(loadMutes()).toEqual(m)
  })

  it("survives corrupt JSON rather than throwing", () => {
    // A throw here would take out every search, since loadMutes runs on the path
    // that builds each request.
    localStorage.setItem("ficatlas:mutes", "{not json")
    expect(loadMutes()).toEqual(EMPTY_MUTES)
  })

  it("fills in fields an older or partial object is missing", () => {
    // The bug this guards: a stored object from before `authors` existed would
    // give `undefined`, and the call site reads `.length` on every field.
    localStorage.setItem("ficatlas:mutes", JSON.stringify({ tags: ["Angst"] }))
    const m = loadMutes()
    expect(m.tags).toEqual(["Angst"])
    expect(m.authors).toEqual([])
    expect(m.relationships).toEqual([])
    expect(() => muteCount(m)).not.toThrow()
  })

  it("discards non-string and blank entries", () => {
    localStorage.setItem("ficatlas:mutes",
      JSON.stringify({ tags: ["Fluff", 42, null, "  ", "Angst"] }))
    expect(loadMutes().tags).toEqual(["Fluff", "Angst"])
  })

  it("ignores a stored value that is not an object", () => {
    localStorage.setItem("ficatlas:mutes", JSON.stringify("nope"))
    expect(loadMutes()).toEqual(EMPTY_MUTES)
  })

  it("ignores a field stored as the wrong type", () => {
    localStorage.setItem("ficatlas:mutes", JSON.stringify({ tags: "Angst" }))
    expect(loadMutes().tags).toEqual([])
  })
})

describe("saveMutes", () => {
  it("announces the change so an open search page can re-run", () => {
    const seen = vi.fn()
    window.addEventListener(MUTES_CHANGED, seen)
    saveMutes(list({ tags: ["Angst"] }))
    expect(seen).toHaveBeenCalledOnce()
  })

  it("does not throw when storage is unavailable", () => {
    // Private mode, or quota exhausted. Losing the write is acceptable;
    // interrupting the reader with an exception is not.
    const spy = vi.spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => { throw new Error("QuotaExceededError") })
    expect(() => saveMutes(list({ tags: ["Angst"] }))).not.toThrow()
    spy.mockRestore()
  })
})

describe("muteCount", () => {
  it("is zero for an empty list", () => {
    expect(muteCount(EMPTY_MUTES)).toBe(0)
  })

  it("counts across every category", () => {
    expect(muteCount(list({
      tags: ["a", "b"], relationships: ["c"], fandoms: ["d"],
      characters: ["e"], authors: ["f"],
    }))).toBe(6)
  })
})

describe("withMutes", () => {
  const apply = (init: string, mutes: MuteList) =>
    withMutes(new URLSearchParams(init), mutes)

  it("adds nothing when the list is empty", () => {
    const p = apply("q=harry", EMPTY_MUTES)
    expect(p.get("exclude_tags")).toBeNull()
    expect(p.get("exclude_authors")).toBeNull()
    expect(p.get("q")).toBe("harry")
  })

  it("maps each category to its API parameter", () => {
    const p = apply("", list({
      tags: ["Character Death"], relationships: ["A/B"], fandoms: ["Original Work"],
      characters: ["Umbridge"], authors: ["SomeAuthor"],
    }))
    expect(p.get("exclude_tags")).toBe("Character Death")
    expect(p.get("exclude_relationships")).toBe("A/B")
    expect(p.get("exclude_fandoms")).toBe("Original Work")
    expect(p.get("exclude_characters")).toBe("Umbridge")
    expect(p.get("exclude_authors")).toBe("SomeAuthor")
  })

  it("merges with exclusions the search already had", () => {
    // The search page's own exclude chips must survive; a mute must not replace
    // what the reader typed for this one search.
    const p = apply("exclude_tags=Angst", list({ tags: ["Fluff"] }))
    expect(p.get("exclude_tags")).toBe("Angst,Fluff")
  })

  it("does not duplicate a value already excluded", () => {
    const p = apply("exclude_tags=Angst", list({ tags: ["Angst"] }))
    expect(p.get("exclude_tags")).toBe("Angst")
  })

  it("dedupes ignoring capitals", () => {
    // The same tag typed into a search and into the mute list should not be sent
    // twice in two capitalisations.
    const p = apply("exclude_tags=angst", list({ tags: ["Angst"] }))
    expect(p.get("exclude_tags")).toBe("angst")
  })

  it("keeps several mutes in one parameter", () => {
    const p = apply("", list({ tags: ["A", "B", "C"] }))
    expect(p.get("exclude_tags")).toBe("A,B,C")
  })

  it("leaves unrelated parameters alone", () => {
    const p = apply("q=harry&sites=ao3&page=2", list({ authors: ["X"] }))
    expect(p.get("q")).toBe("harry")
    expect(p.get("sites")).toBe("ao3")
    expect(p.get("page")).toBe("2")
  })

  it("ignores empty segments in an existing exclusion", () => {
    const p = apply("exclude_tags=,,Angst,", list({ tags: ["Fluff"] }))
    expect(p.get("exclude_tags")).toBe("Angst,Fluff")
  })

  it("mutates and returns the same params object", () => {
    const original = new URLSearchParams("q=x")
    const returned = withMutes(original, list({ tags: ["A"] }))
    expect(returned).toBe(original)
  })
})
