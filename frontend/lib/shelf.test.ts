import { describe, it, expect } from "vitest"
import { buildShelf, shelfHas, type ShelfFilter } from "./shelf"

/**
 * The real shelf of the only account that has one, as exported from
 * user_data + user_hosted on 2026-09-28. Ids are real; titles are not stored
 * in user_hosted and are irrelevant to the merge, which keys on id.
 *
 * This fixture is the ARGUMENT for the redesign, so it is asserted rather than
 * described: four tabs showed 43 listings of 32 works.
 */
const IMPORTS = `e6d3e1f5-e0dd-4e39-9d88-2cd33e9159f7 0ed68727-ece5-450e-8b2b-e338bb9e48d1
4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c 2275a94a-db85-40d3-9b2e-0552b4bc5cda
8fd5a165-5d5c-486b-99a8-aef43067c151 f22216f6-8543-4934-b746-d91b3ba7d85d
59681dc9-9163-4cd3-a150-fe96858990d5 a6cb0749-7ee1-4eec-9af3-4ce0c2913f26
9e56dc13-351e-496e-91ee-fdaa0a01e370 b42f519c-e3d1-48fd-ab62-64751ef6c6f1
94be3074-422b-4322-8bd1-f4dddf0af4d1 504247cc-488e-49f7-bc31-62f6808f115e
65526f90-0a7b-491a-869a-54eb3787b773 893ab135-621d-4593-a5e6-ccd82636c0f4
8f916395-eae5-4e1f-bfe2-e1f6dbbdd094 b25f6bfe-aec3-494f-bf69-4348865f7000
eeaee2be-dbf4-4bd7-91fd-1e8357d47a03 5fc1a98a-49d6-4260-94ae-ebbb588bfe43
14f64996-02c9-43f9-9701-c12a444eb023 0dca8795-fe34-4e6a-9552-d5dcadfaec09
844e5508-9ff6-4fea-86ed-83c86c20d6ea 0f0e035e-aadb-4cdc-8899-d36d02e2f7d2
5cc8a80c-23eb-4215-8262-e24d28abbd0c 8a03a781-17f6-4eb6-9a4a-28fd920f2bb8
495f050d-deae-4392-88e0-2e878cc1276d 4f7e42ab-c2b0-4f0c-83b7-37bd0fde1a10
28549852-2595-4215-bbee-c025ba0dadfa 476509a6-af91-4f26-a500-ca8aa4d4946b
c239ae25-58b9-45be-9db3-a36d33f978aa`.split(/\s+/)

const PROGRESS_IDS = [
  "069245a5-fa6a-44ad-828d-7331333aa43d", "0ed68727-ece5-450e-8b2b-e338bb9e48d1",
  "31f82fcf-6478-4eb2-a2d9-6dbeb89fcbf1", "476509a6-af91-4f26-a500-ca8aa4d4946b",
  "4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c", "6ca9b972-66a5-4179-a8e2-34666c1afaf6",
  "c239ae25-58b9-45be-9db3-a36d33f978aa", "e6d3e1f5-e0dd-4e39-9d88-2cd33e9159f7",
  "f22216f6-8543-4934-b746-d91b3ba7d85d",
]
const OFFLINE_IDS = [
  "31f82fcf-6478-4eb2-a2d9-6dbeb89fcbf1", "4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c",
  "069245a5-fa6a-44ad-828d-7331333aa43d", "0ed68727-ece5-450e-8b2b-e338bb9e48d1",
]
const BOOKMARK_IDS = ["4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c"]

const REAL = {
  imports: IMPORTS.map((id, i) => ({ id, title: `Import ${i}`, author: "A",
                                     added_at: `2026-09-0${(i % 9) + 1}T00:00:00Z` })),
  bookmarks: BOOKMARK_IDS.map(id => ({ id, title: "Bookmarked work", savedAt: "2026-09-20" })),
  progress: Object.fromEntries(PROGRESS_IDS.map((id, i) =>
    [id, { chapter: i + 1, at: "2026-09-25T00:00:00Z", title: `Reading ${i}` }])),
  offline: OFFLINE_IDS.map(id => ({ id, title: "Saved", savedAt: 1758960000000 })),
}

const count = (items: ReturnType<typeof buildShelf>, f: ShelfFilter) =>
  items.filter(it => shelfHas(it, f)).length

describe("the shelf merge, against the real account", () => {
  it("is 32 works where four tabs showed 43 listings", () => {
    const shelf = buildShelf(REAL)
    const listings = REAL.imports.length + REAL.bookmarks.length
                   + Object.keys(REAL.progress).length + REAL.offline.length
    expect(listings).toBe(43)
    expect(shelf.length).toBe(32)
  })

  it("keeps every source's own count intact as a filter", () => {
    const shelf = buildShelf(REAL)
    expect(count(shelf, "mine")).toBe(29)
    expect(count(shelf, "reading")).toBe(9)
    expect(count(shelf, "offline")).toBe(4)
    expect(count(shelf, "bookmarks")).toBe(1)
    expect(count(shelf, "all")).toBe(32)
  })

  it("puts the one bookmark in all three states on ONE row", () => {
    // This work is the whole case: it was listed in Bookmarks, in Reading and
    // in Offline, and nothing on any of those screens said so.
    const it = buildShelf(REAL).find(x => x.id === BOOKMARK_IDS[0])!
    expect(it.bookmark).toBeTruthy()
    expect(it.progress).toBeTruthy()
    expect(it.offline).toBeTruthy()
    expect(it.imported).toBeTruthy()
  })

  it("never lists one work twice", () => {
    const ids = buildShelf(REAL).map(x => x.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe("the merge itself", () => {
  it("merges on id, not on title — five works here are called Manacled", () => {
    const shelf = buildShelf({
      bookmarks: [{ id: "a", title: "Manacled" }],
      progress: { b: { chapter: 2, at: "2026-01-01", title: "Manacled" } },
    })
    expect(shelf.length).toBe(2)
  })

  it("fills a field from whichever source has it", () => {
    const shelf = buildShelf({
      // Progress knows a title and nothing else; the bookmark knows the author.
      progress: { a: { chapter: 3, at: "2026-01-01", title: "A Work" } },
      bookmarks: [{ id: "a", title: "A Work", author: "Someone", site: "ao3" }],
    })
    expect(shelf).toHaveLength(1)
    expect(shelf[0].author).toBe("Someone")
    expect(shelf[0].site).toBe("ao3")
    expect(shelf[0].progress?.chapter).toBe(3)
  })

  it("reads savedAt whether it is an epoch number or an ISO string", () => {
    // Date.parse of a number is NaN, which would sort every download to the
    // bottom of "Recently added" and look like the sort was broken.
    const n = buildShelf({ offline: [{ id: "a", title: "T", savedAt: 1758960000000 }] })
    const s = buildShelf({ offline: [{ id: "a", title: "T", savedAt: "2026-09-27T08:00:00Z" }] })
    expect(n[0].added).toBe(1758960000000)
    expect(s[0].added).toBeGreaterThan(0)
  })

  it("survives every source being empty", () => {
    expect(buildShelf({})).toEqual([])
  })
})
