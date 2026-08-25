import { describe, it, expect } from "vitest"
import { parseQuery, canonicalSite, quoteValue } from "./queryParser"

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

// ── Quoting: only when the value would not round-trip bare ──────────────────
// The serialiser used to quote anything containing a space, which filled the bar
// with `fandom:"Harry Potter"` for the commonest search there is. These pin the
// three cases where a quote is actually load-bearing, and the round trip itself.
describe("quoteValue", () => {
  it("leaves an ordinary multi-word value bare", () => {
    expect(quoteValue("Harry Potter")).toBe("Harry Potter")
    expect(quoteValue("Hermione Granger")).toBe("Hermione Granger")
    expect(quoteValue("Bahasa Indonesia")).toBe("Bahasa Indonesia")
  })

  it("quotes a value whose last word is shorthand the parser would strip", () => {
    expect(quoteValue("Nothing Is Complete")).toBe('"Nothing Is Complete"')
    expect(quoteValue("Project Wip")).toBe('"Project Wip"')
  })

  it("quotes a value containing something that reads as an operator key", () => {
    // `tag:` IS an operator alias, so leaving this bare would end the fandom
    // value early and start a tag filter.
    expect(quoteValue("re: zero tag: x")).toBe('"re: zero tag: x"')
    // `Trek:` is not an alias, so this needs no quotes and should not get any.
    expect(quoteValue("Star Trek: Voyager")).toBe("Star Trek: Voyager")
  })

  it("quotes a value containing a quote", () => {
    expect(quoteValue('The "Real" Thing')).toBe('"The "Real" Thing"')
  })

  it("round-trips a bare multi-word fandom with another operator after it", () => {
    const s = `fandom:${quoteValue("Harry Potter")} char:${quoteValue("Hermione Granger")}`
    expect(s).toBe("fandom:Harry Potter char:Hermione Granger")
    const pq = parseQuery(s)
    expect(pq.fandoms).toEqual(["Harry Potter"])
    expect(pq.characters).toEqual(["Hermione Granger"])
  })

  it("round-trips a value that needed the quotes", () => {
    const pq = parseQuery(`fandom:${quoteValue("Nothing Is Complete")}`)
    expect(pq.fandoms).toEqual(["Nothing Is Complete"])
    expect(pq.status).toBeNull()
  })
})

// ── Every filter the panel can set must survive the round trip ──────────────
// The search bar REPLACES its own contents from the filter state, so a filter it
// cannot write is a filter that gets silently dropped the next time anything
// re-runs the search. These pin the operators for the five that used to be
// unwritable: warnings, categories, sites, crossovers and updated-after.
describe("filters the bar writes must parse back", () => {
  it("warnings", () => {
    const pq = parseQuery("warn:Underage")
    expect(pq.warnings).toEqual(["Underage"])
  })

  it("categories", () => {
    const pq = parseQuery("cat:F/F")
    expect(pq.categories).toEqual(["F/F"])
  })

  it("sites, one operator per archive", () => {
    const pq = parseQuery("site:ao3 site:fictionalley")
    expect(pq.sites).toEqual(["ao3", "fictionalley"])
  })

  it("crossovers both ways", () => {
    expect(parseQuery("xover:only").crossovers).toBe("only")
    expect(parseQuery("xover:exclude").crossovers).toBe("exclude")
  })

  it("updated-after keeps the exact date the panel stored", () => {
    expect(parseQuery("updated:2026-01-31").updatedAfter).toBe("2026-01-31")
  })

  it("a multi-word warning still round-trips with the quoting rule", () => {
    const s = `warn:${quoteValue("Major Character Death")}`
    expect(s).toBe("warn:Major Character Death")
    expect(parseQuery(s).warnings).toEqual(["Major Character Death"])
  })

  it("all five together, alongside the older operators", () => {
    const pq = parseQuery(
      "fandom:Harry Potter warn:Underage cat:M/M site:ao3 xover:exclude updated:2026-01-31")
    expect(pq.fandoms).toEqual(["Harry Potter"])
    expect(pq.warnings).toEqual(["Underage"])
    expect(pq.categories).toEqual(["M/M"])
    expect(pq.sites).toEqual(["ao3"])
    expect(pq.crossovers).toBe("exclude")
    expect(pq.updatedAfter).toBe("2026-01-31")
  })
})
