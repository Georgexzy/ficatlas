import { describe, it, expect } from "vitest"
import { parseQuery, canonicalSite } from "./queryParser"

// This parser has a twin: backend/query_parser.py. The search bar parses a query
// here to render its chips and build the URL, and the API re-parses the same
// string on the way in. When the two disagree, a query means one thing while you
// are typing it and another when it runs — and a shared link means something
// different again. The cases below are deliberately the same ones asserted in
// backend/tests/test_query_parser.py.

describe("site: aliases", () => {
  it("passes the canonical values through", () => {
    expect(parseQuery("site:ao3").sites).toEqual(["ao3"])
    expect(parseQuery("site:ffnet").sites).toEqual(["ffnet"])
    expect(parseQuery("site:fictionalley").sites).toEqual(["fictionalley"])
  })

  it("is case insensitive", () => {
    expect(parseQuery("site:AO3").sites).toEqual(["ao3"])
  })

  it("accepts the domain someone would paste", () => {
    expect(parseQuery("site:fanfiction.net").sites).toEqual(["ffnet"])
    expect(parseQuery("site:archiveofourown.org").sites).toEqual(["ao3"])
  })

  it("accepts the common abbreviations", () => {
    expect(parseQuery("site:ffn").sites).toEqual(["ffnet"])
    expect(parseQuery("site:ff.net").sites).toEqual(["ffnet"])
    expect(parseQuery("site:ficalley").sites).toEqual(["fictionalley"])
  })

  it("accepts the digit-zero misreading of AO3", () => {
    expect(parseQuery("site:a03").sites).toEqual(["ao3"])
  })

  it("accepts a quoted multi-word name", () => {
    expect(parseQuery('site:"archive of our own"').sites).toEqual(["ao3"])
  })

  it("drops an unknown archive rather than matching nothing", () => {
    const pq = parseQuery("site:goodreads harry potter")
    expect(pq.sites).toEqual([])
    expect(pq.cleanText).toBe("harry potter")
  })

  it("shows the resolved archive on the token", () => {
    const tok = parseQuery("site:ff.net").tokens[0]
    expect(tok.key).toBe("sites")
    expect(tok.value).toBe("ffnet")
  })

  it("canonicalSite returns null for an unknown name", () => {
    expect(canonicalSite("goodreads")).toBeNull()
    expect(canonicalSite("AO3")).toBe("ao3")
  })
})

describe("single-token operators do not swallow the query", () => {
  // The bug: a bare value ran to the next operator key, which is right for
  // `fandom: Harry Potter` and wrong for every operator with a fixed set of
  // values. `rating:M harry potter` took "M harry potter" as the rating, so
  // nothing matched it AND no search text was left — the bar quietly searched
  // the whole index.
  it("site: leaves the trailing text alone", () => {
    const pq = parseQuery("site:ao3 harry potter")
    expect(pq.sites).toEqual(["ao3"])
    expect(pq.cleanText).toBe("harry potter")
  })

  it("rating: leaves the trailing text alone", () => {
    const pq = parseQuery("rating:M harry potter")
    expect(pq.ratings).toEqual(["M"])
    expect(pq.cleanText).toBe("harry potter")
  })

  it("status: leaves the trailing text alone", () => {
    const pq = parseQuery("status:complete harry potter")
    expect(pq.status).toBe("complete")
    expect(pq.cleanText).toBe("harry potter")
  })

  it("updated: leaves the trailing text alone", () => {
    const pq = parseQuery("updated:2024 harry potter")
    expect(pq.updatedAfter).toBe("2024-01-01")
    expect(pq.cleanText).toBe("harry potter")
  })

  it("words: leaves the trailing text alone", () => {
    const pq = parseQuery("words:>100k harry potter")
    expect(pq.wordCountMin).toBe(100000)
    expect(pq.cleanText).toBe("harry potter")
  })
})

describe("multi-word operators still take multi-word values", () => {
  // The other half of the same rule: narrowing every operator to one word would
  // break the syntax the README leads with.
  it("fandom: takes a bare multi-word value", () => {
    const pq = parseQuery("fandom: Harry Potter complete >100k")
    expect(pq.fandoms).toEqual(["Harry Potter"])
    expect(pq.status).toBe("complete")
    expect(pq.wordCountMin).toBe(100000)
  })

  it("language keeps multi-word values, since 'Bahasa Indonesia' is one", () => {
    expect(parseQuery("lang:Bahasa Indonesia").language).toBe("Bahasa Indonesia")
  })

  it("combines a site filter with other operators", () => {
    const pq = parseQuery("site:FF.net fandom:Naruto complete >100k")
    expect(pq.sites).toEqual(["ffnet"])
    expect(pq.fandoms).toEqual(["Naruto"])
    expect(pq.status).toBe("complete")
    expect(pq.wordCountMin).toBe(100000)
  })
})
