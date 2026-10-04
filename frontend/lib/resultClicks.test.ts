import { describe, expect, it } from "vitest"
import { clickFields, opensTheWork } from "./resultClicks"

describe("result click beacon", () => {
  it("counts opening the work, here or on its archive", () => {
    expect(opensTheWork("/story/abc")).toBe(true)
    expect(opensTheWork("/story/abc/chapter/1")).toBe(true)
    expect(opensTheWork("https://archiveofourown.org/works/123")).toBe(true)
    expect(opensTheWork("https://www.fanfiction.net/s/9/1/")).toBe(true)
  })

  it("does not count refining the search", () => {
    expect(opensTheWork("/?fandoms=Naruto")).toBe(false)
    expect(opensTheWork("/?author=x")).toBe(false)
    expect(opensTheWork("/series/abc")).toBe(false)
    expect(opensTheWork("https://archiveofourown.org/users/someone")).toBe(false)
  })

  it("sends the work, the position and the search parameters", () => {
    expect(clickFields("https://archiveofourown.org/works/1", "id1", 23, "?q=drarry&page=2"))
      .toEqual({ path: "/story/id1", kind: "click", pos: "23", search: "?q=drarry&page=2" })
    expect(clickFields("/story/id1", "id1", 0, "?q=x")).toBeNull()
    expect(clickFields("/story/id1", undefined, 3, "?q=x")).toBeNull()
  })
})
