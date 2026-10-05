import { describe, it, expect } from "vitest"
import { fitReply, sourceOf, where } from "./outreachSources"

describe("outreach sources", () => {
  it("defaults anything unknown to Reddit, which every older row is", () => {
    expect(sourceOf({})).toBe("reddit")
    expect(sourceOf({ source: "bluesky" })).toBe("bluesky")
  })

  it("names where a post lives", () => {
    expect(where({ subreddit: "FanFiction" })).toBe("r/FanFiction")
    expect(where({ source: "tumblr", subreddit: "tumblr", flair: "fic finder" }))
      .toBe("Tumblr · #fic finder")
  })

  it("keeps a Bluesky reply under 300 and never cuts the link", () => {
    const link = "https://ficatlas.com/?q=" + "x".repeat(60)
    const r = fitReply("bluesky", "long reply", "a".repeat(400), link)
    expect([...r].length).toBeLessThanOrEqual(300)
    expect(r.endsWith(link)).toBe(true)
  })

  it("leaves Reddit and Tumblr replies whole", () => {
    expect(fitReply("reddit", "full text", "short", "L")).toBe("full text")
    expect(fitReply("tumblr", "full text", "short", "L")).toBe("full text")
  })
})
