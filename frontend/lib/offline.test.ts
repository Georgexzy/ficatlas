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
import {
  savedStoryToDetail, SCHEMA_VERSION,
  offlineHoles, offlineRetryable, isUsableRecordForTest as isUsableRecord,
  type OfflineStory,
} from "./offline"

// The smallest thing that must be accepted, so the validator tests are about
// the one field they are checking rather than about assembling a full record.
const VALID = { id: "abc", title: "A Work", author: "Someone", site: "ao3",
                url: "https://example.org/abc", chapters: [{ number: 1, content: "text" }] }

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
// ── what a reader is actually missing ───────────────────────────────────────
//
// Two of these functions exist because three call sites each had their own
// `?.length ?? 0` against a different field, and a story with a hole in the
// middle of it came to claim it was completely saved. One definition of "hole"
// is the fix; the tests are what stops the third call site reappearing.

describe("offlineHoles — every chapter the reader cannot read", () => {
  it("counts retryable and permanent holes together", () => {
    // From where the reader sits these are the same fact: the chapter is not on
    // this device. They are stored apart because resume treats them oppositely,
    // not because the reader should be told apart.
    expect(offlineHoles({ missingChapters: [4, 5], absentChapters: [9] } as any)).toBe(3)
  })

  it("is zero for a record with no holes, and for a record with neither field", () => {
    expect(offlineHoles({} as any)).toBe(0)
    expect(offlineHoles(null)).toBe(0)
    expect(offlineHoles(undefined)).toBe(0)
    expect(offlineHoles({ missingChapters: [], absentChapters: [] } as any)).toBe(0)
  })

  it("counts a permanent hole even when nothing is retryable", () => {
    // The case this whole split exists for: the download is as complete as it
    // will ever be, and the reader must still be told there is a gap.
    expect(offlineHoles({ absentChapters: [1, 2, 3] } as any)).toBe(3)
  })
})

describe("offlineRetryable — only what another attempt could fetch", () => {
  it("ignores absent chapters entirely", () => {
    // If this counted absent chapters, `resumeInterruptedDownloads` would
    // re-download every work with a deleted chapter in it on every page load,
    // for ever, chasing a chapter the archive does not have.
    expect(offlineRetryable({ absentChapters: [1, 2, 3] } as any)).toBe(0)
  })

  it("counts only the retryable ones when both are present", () => {
    expect(offlineRetryable({ missingChapters: [7, 8], absentChapters: [1] } as any)).toBe(2)
  })

  it("agrees with offlineHoles whenever there is nothing permanent", () => {
    for (const rec of [{}, { missingChapters: [1] }, { missingChapters: [1, 2, 3] }]) {
      expect(offlineRetryable(rec as any)).toBe(offlineHoles(rec as any))
    }
  })
})

describe("a saved record must be readable by the rest of the module", () => {
  // The consequence of getting this wrong is not a bad story, it is a blank
  // page: `savedStoryToDetail` does `.length` and `.map` on `chapters` during a
  // React render, and `auditOfflineStories` filters over it. A malformed record
  // restored from a hand-edited export throws inside both, and the audit's throw
  // is swallowed — so one bad record stops the Library reporting damage to any
  // record.
  it("rejects a record whose chapters are not an array", () => {
    expect(isUsableRecord({ ...VALID, chapters: "not an array" })).toBe(false)
    expect(isUsableRecord({ ...VALID, chapters: {} })).toBe(false)
    expect(isUsableRecord({ ...VALID, chapters: undefined })).toBe(false)
  })

  it("rejects a record with no id or no title", () => {
    expect(isUsableRecord({ ...VALID, id: "" })).toBe(false)
    expect(isUsableRecord({ ...VALID, id: 42 })).toBe(false)
    expect(isUsableRecord({ ...VALID, title: undefined })).toBe(false)
    expect(isUsableRecord(null)).toBe(false)
    expect(isUsableRecord("a string")).toBe(false)
  })

  it("rejects a chapter with no usable number", () => {
    // Chapter numbers are compared with `===` against a route parameter, so a
    // string "3" is a chapter that can never be found again.
    expect(isUsableRecord({ ...VALID, chapters: [{ number: "3", content: "x" }] })).toBe(false)
    expect(isUsableRecord({ ...VALID, chapters: [{ content: "x" }] })).toBe(false)
  })

  it("accepts a record with an empty chapter list", () => {
    // Eviction can empty a record, and that is a real state the audit reports
    // rather than a malformed one.
    expect(isUsableRecord({ ...VALID, chapters: [] })).toBe(true)
  })

  it("accepts a well-formed record", () => {
    expect(isUsableRecord(VALID)).toBe(true)
  })
})
