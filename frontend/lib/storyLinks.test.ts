import { describe, it, expect } from "vitest"
import { storyLink, isUploadUrl } from "./storyLinks"

// ── Uploaded works have no external source ──────────────────────────────────
// A `ficatlas://upload/<uuid>` row is an EPUB uploaded here. It used to fall
// through to the default branch and render "Read on AO3 ↗" pointing at a scheme
// no browser can open — a dead button beside the working "Read here" on the
// owner's own upload.
describe("uploaded works", () => {
  const upload = {
    id: "abc-123",
    url: "ficatlas://upload/0ed68727-ece5-450e-8b2b-e338bb9e48d1",
    site: "ao3",
    title: "My Upload",
    author: "Someone",
  }

  it("is recognised as an upload", () => {
    expect(isUploadUrl(upload.url)).toBe(true)
    expect(isUploadUrl("https://archiveofourown.org/works/1")).toBe(false)
    expect(isUploadUrl("seed://janelleshane/x")).toBe(false)
    expect(isUploadUrl(null)).toBe(false)
  })

  it("is flagged as having no external source", () => {
    expect(storyLink(upload).isInternal).toBe(true)
  })

  it("never hands back the unopenable scheme as an href", () => {
    // Belt and braces: a caller that ignores isInternal must still get
    // something a browser can follow.
    const t = storyLink(upload)
    expect(t.href.startsWith("ficatlas://")).toBe(false)
    expect(t.href).toBe("/story/abc-123")
  })

  it("leaves ordinary works alone", () => {
    const ao3 = {
      id: "x", url: "https://archiveofourown.org/works/123",
      site: "ao3", title: "T", author: "A",
    }
    const t = storyLink(ao3)
    expect(t.isInternal).toBeFalsy()
    expect(t.href).toBe("https://archiveofourown.org/works/123")
  })

  it("leaves seed rows on their AO3 search", () => {
    const seed = { id: "y", url: "seed://janelleshane/x", site: "ao3", title: "T" }
    const t = storyLink(seed)
    expect(t.isSearch).toBe(true)
    expect(t.isInternal).toBeFalsy()
  })
})

import { otherCopies } from "./storyLinks"

describe("otherCopies", () => {
  const labels = { ao3: "AO3", ffnet: "FF.net", fictionalley: "FictionAlley" }

  it("names the archive of each copy and links it", () => {
    const c = otherCopies({ site: "ao3", url: "https://archiveofourown.org/works/1",
      cross_post_urls: ["https://www.fanfiction.net/s/9/1/"] }, labels)
    expect(c).toEqual([{ site: "ffnet", label: "FF.net",
      href: "https://www.fanfiction.net/s/9/1/", sameArchive: false }])
  })

  it("drops placeholders, junk and the work's own address", () => {
    expect(otherCopies({ site: "ffnet", url: "https://www.fanfiction.net/s/8/1/",
      cross_post_urls: ["seed://janelleshane/x", "&#039;https:", "ficatlas://upload/u",
                        "http://www.fanfiction.net/s/8/1"] }, labels)).toEqual([])
  })

  it("tells a repost on the same archive apart, and lists it last", () => {
    const c = otherCopies({ site: "ao3", url: "https://archiveofourown.org/works/1",
      cross_post_urls: ["https://archiveofourown.org/works/2",
                        "https://www.fanfiction.net/s/9/1/"] }, labels)
    expect(c.map(x => [x.site, x.sameArchive])).toEqual([["ffnet", false], ["ao3", true]])
  })

  it("keeps an unknown mirror, labelled by its host", () => {
    const c = otherCopies({ site: "ao3", url: "",
      cross_post_urls: ["https://www.example-archive.org/story/5"] }, labels)
    expect(c[0]).toMatchObject({ site: "other", label: "example-archive.org" })
  })
})
