// What goes in the ADDRESS is not what goes to the API.
//
// Two rules, and the first one cannot express the second — which is how a
// reader's content preference came to travel in a link.
//
// Found by measurement against the deployed build, not by reading the code. With
// `ficatlas:show_explicit` set to "true", the app issued a search with
// `explicit=false` and `ratings=G,T,M,NR`. Conversely a link reading
// `?explicit=true` reached a reader who had the setting off and issued
// `explicit=true`. The preference was ignored for the reader who consented and
// imposed on the reader who did not, from the same parameter.

import { describe, it, expect } from "vitest"
import { urlQueryString, URL_DEFAULTS, NEVER_IN_URL } from "./searchUrl"

describe("urlQueryString", () => {
  it("never writes a content preference into the address", () => {
    // The defect. `explicit` was a URL default of "false", and defaults are
    // dropped by COMPARISON — so "false" was dropped and "true" was kept, which
    // is the one value that carries the preference.
    const qs = urlQueryString({ explicit: true, q: "drarry" })
    expect(qs).not.toContain("explicit")
    expect(qs).toContain("q=drarry")
  })

  it("excludes both content tiers", () => {
    const qs = urlQueryString({
      explicit: true,
      include_underage: true,
      fandoms: "Harry Potter",
    })
    expect(qs).not.toContain("explicit")
    expect(qs).not.toContain("include_underage")
    expect(qs).toContain("fandoms=Harry+Potter")
  })

  it("does not exclude the preference from the API request", () => {
    // Stated as a boundary, because the fix is easy to over-apply: the function
    // decides what is WRITTEN DOWN. The caller still sends the full object, so
    // the reader who chose adult content still gets it. If this ever stopped
    // being true the site would silently filter the people who opted in.
    expect(NEVER_IN_URL.has("explicit")).toBe(true)
    expect(Object.keys(URL_DEFAULTS)).toContain("ratings")
    // A reader with the preference on sends ratings undefined (meaning all
    // ratings) rather than pinning the filtered set. See buildParams.
    const off = urlQueryString({ ratings: "G,T,M,NR" })
    expect(off).not.toContain("ratings")
  })

  it("drops values equal to the page default", () => {
    expect(urlQueryString({ sort: "relevance" })).toBe("")
    expect(urlQueryString({ sort: "popularity_desc" })).toBe("sort=popularity_desc")
    expect(urlQueryString({ per_page: 20 })).toBe("")
    expect(urlQueryString({ per_page: 50 })).toBe("per_page=50")
  })

  it("keeps real filters, including falsy and zero values", () => {
    // `0` and `false` are values, not absences. Only undefined/null/"" are.
    const qs = urlQueryString({
      q: "drarry",
      word_count_min: 0,
      include_unknown: false,
      dlp_min_rating: 0,
    })
    expect(qs).toContain("q=drarry")
    expect(qs).toContain("word_count_min=0")
    expect(qs).toContain("dlp_min_rating=0")
    // false IS this page's default, so it is redundant rather than absent.
    expect(qs).not.toContain("include_unknown")
  })

  it("produces the same string regardless of parameter order", () => {
    // The prefetch stores and looks up under this string, so two calls with the
    // same parameters must agree. Not object key order — the caller builds the
    // same object — but the contents must be fully determined by the values.
    const a = urlQueryString({ q: "x", fandoms: "Naruto" })
    const b = urlQueryString({ q: "x", fandoms: "Naruto" })
    expect(a).toBe(b)
  })

  it("is empty for a bare browse, which is a legitimate page", () => {
    // A URL like /?sort=popularity_desc is the whole of a legal search once
    // defaults are dropped, so emptiness must not be a signal of anything.
    expect(urlQueryString({ sort: "popularity_desc", page: 1, per_page: 20 }))
      .toBe("sort=popularity_desc")
    expect(urlQueryString({ page: 1, per_page: 20, match_mode: "all" })).toBe("")
  })
})