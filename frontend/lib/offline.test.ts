// The offline story page must not assert what it does not know.
//
// Found by measuring rather than reading: with the network cut, a saved work with
// 1,039 kudos, 59,744 hits, 12 characters, 3 pairings and 6 tags rendered with no
// kudos row, no hits, no cast and no pairings, and "Not stated" where the archive
// says Complete. Nothing threw and the page looked fine — which is why it survived
// every code review that could have caught it.
//
// Two things are asserted, and the second is the one that matters:
//
//   1. A record written by the current save carries everything the page shows.
//   2. A field the record does NOT carry is absent, not invented. The first test
//      alone would still pass against the old code if it only checked the fields
//      it happened to store.

import { describe, it, expect } from "vitest"
import { savedStoryToDetail, SCHEMA_VERSION, type OfflineStory } from "./offline"

const base = (over: Partial<OfflineStory> = {}): OfflineStory => ({
  id: "story-1",
  title: "Starry Skies and Gardenias",
  author: "Emekasign",
  site: "ao3",
  url: "https://example.invalid/story-1",
  chapter_count: 4,
  chapters: [
    { number: 1, title: "Chapter 1", content: "a" },
    { number: 2, title: "Chapter 2", content: "b" },
    { number: 3, title: "Chapter 3", content: "c" },
    { number: 4, title: "Chapter 4", content: "d" },
  ],
  savedAt: "2026-10-01T00:00:00.000Z",
  schema: SCHEMA_VERSION,
  ...over,
})

describe("savedStoryToDetail", () => {
  it("carries the metadata the story page renders, from a current record", () => {
    const d = savedStoryToDetail(base({
      chapter_count_total: 199,
      status: "complete",
      rating: "NR",
      kudos: 1039,
      hits: 59744,
      updated_at: "2022-12-22T00:00:00",
      fandoms: ["Harry Potter - J. K. Rowling"],
      relationships: ["Sirius Black/Remus Lupin"],
      characters: ["Regulus Black", "Remus Lupin"],
      tags: ["Slow Burn", "Fluff and Angst"],
      warnings: ["Creator Chose Not To Use Archive Warnings"],
      word_count: 1119436,
    })) as any

    expect(d.status).toBe("complete")
    expect(d.kudos).toBe(1039)
    expect(d.hits).toBe(59744)
    expect(d.rating).toBe("NR")
    expect(d.updated_at).toBe("2022-12-22T00:00:00")
    expect(d.relationships).toEqual(["Sirius Black/Remus Lupin"])
    expect(d.characters).toHaveLength(2)
    expect(d.tags).toHaveLength(2)
    expect(d.warnings).toHaveLength(1)
    expect(d.word_count).toBe(1119436)
  })

  it("reports held chapters against the work's real length", () => {
    // The page renders `${chapter_count}/${chapter_count_total}`. With only the
    // held count it read "4/?", which said nothing about whether a partial save
    // was the whole work.
    const d = savedStoryToDetail(base({ chapter_count_total: 199 })) as any
    expect(d.chapter_count).toBe(4)
    expect(d.chapter_count_total).toBe(199)
    expect(`${d.chapter_count}/${d.chapter_count_total}`).toBe("4/199")
  })

  it("does not invent a kudos figure it does not have", () => {
    // The regression, stated as an invariant. The page hides kudos under
    // `kudos > 0`, so a substituted 0 does not merely show a wrong number — it
    // deletes the evidence that the work is read at all.
    const d = savedStoryToDetail(base()) as any
    expect(d.kudos ?? 0).toBe(0)
    expect(d.hits ?? 0).toBe(0)
  })

  it("keeps pre-v3 records rendering exactly as they did", () => {
    // Readers who have not re-saved must not see a changed page: a record written
    // before v3 genuinely has none of these fields, so the old defaults stand.
    const old = base({ schema: 1 }) as any
    delete old.kudos; delete old.hits; delete old.relationships
    delete old.characters; delete old.tags; delete old.chapter_count_total
    const d = savedStoryToDetail(old) as any
    expect(d.status).toBe("unknown")
    expect(d.kudos).toBe(0)
    expect(d.relationships).toEqual([])
    expect(d.characters).toEqual([])
    expect(d.tags).toEqual([])
    expect(d.chapter_count_total).toBeUndefined()
  })

  it("maps every saved chapter to a navigable chapter entry", () => {
    const d = savedStoryToDetail(base()) as any
    expect(d.chapters).toHaveLength(4)
    expect(d.chapters.map((c: any) => c.number)).toEqual([1, 2, 3, 4])
    // ids must be unique or React keys collide and the chapter list renders wrong.
    expect(new Set(d.chapters.map((c: any) => c.id)).size).toBe(4)
  })

  it("counts chapters it holds, not chapters the record claims", () => {
    // `chapter_count` on the record is written by the save; a record whose array
    // and count disagree must be read from the array, because that is what the
    // reader can actually open.
    const d = savedStoryToDetail(base({ chapter_count: 199 })) as any
    expect(d.chapter_count).toBe(4)
  })
})

describe("SCHEMA_VERSION", () => {
  it("is 3, because the record shape changed", () => {
    // v3 added the story metadata. A record stamped 2 does not carry it, and a
    // migration that assumes otherwise would read undefined as a real value.
    expect(SCHEMA_VERSION).toBe(3)
  })
})