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
