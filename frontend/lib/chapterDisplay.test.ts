import { describe, it, expect } from "vitest"
import { chapterDisplay } from "./api"

describe("chapterDisplay", () => {
  it("a finished work's count is its total, never 'N/?'", () => {
    expect(chapterDisplay(29, null, "complete")).toBe("29")
  })
  it("an unfinished work without a total keeps the question mark", () => {
    expect(chapterDisplay(12, null, "in_progress")).toBe("12/?")
    expect(chapterDisplay(12)).toBe("12/?")
  })
  it("a stated total always wins", () => {
    expect(chapterDisplay(12, 30, "in_progress")).toBe("12/30")
  })
})
