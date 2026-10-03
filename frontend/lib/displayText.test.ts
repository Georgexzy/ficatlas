import { describe, it, expect } from "vitest"
import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import { clampQuery, QUERY_CLAMP } from "./displayText"

// The results state's hidden <h1> and the visible "Searching …" line both quote
// the text in the search bar, so they must quote the same amount of it. That is
// one function rather than two `slice` calls, because the second one is
// invisible when it drifts: the heading is visually hidden, so nothing about it
// shows up in a layout measurement and reading the JSX is the only way to see
// the disagreement. It was 72 characters for a 60-character query.

describe("clampQuery", () => {
  it("leaves a short query alone", () => {
    expect(clampQuery("drarry")).toBe("drarry")
  })

  it("leaves a query exactly at the clamp alone", () => {
    const exact = "x".repeat(QUERY_CLAMP)
    expect(clampQuery(exact)).toBe(exact)
    expect(clampQuery(exact)).not.toContain("…")
  })

  it("cuts one character past the clamp", () => {
    expect(clampQuery("x".repeat(QUERY_CLAMP + 1))).toBe("x".repeat(QUERY_CLAMP) + "…")
  })

  it("trims first, so padding cannot buy extra length", () => {
    expect(clampQuery("   drarry   ")).toBe("drarry")
  })

  it("keeps a long query to the clamp plus the ellipsis", () => {
    // The defect in one assertion: the heading was built from the raw query.
    const heading = `Results for ${clampQuery("a".repeat(60))}`
    expect(heading.length).toBeLessThanOrEqual("Results for ".length + QUERY_CLAMP + 1)
  })

  it("returns an empty string for an empty query", () => {
    expect(clampQuery("   ")).toBe("")
  })
})

// The behavioural tests above cannot see the call sites. This can, so it guards
// against someone reintroducing an inline slice at one of the two.
//
// It reads `app/page.tsx`, which is only the WORKING COPY when `app/` is
// mounted. Run it that way:
//
//   docker compose run --rm --no-deps -T \
//     -v "$PWD/frontend/lib:/app/lib" -v "$PWD/frontend/app:/app/app" \
//     frontend npx vitest run
//
// Without the mount it reads whatever the image was built from, and a stale
// image would report a fault that was already fixed. Rather than fail on that,
// it skips when the file plainly predates the heading.
const PAGE = path.resolve(__dirname, "../app/page.tsx")
const pageSrc = existsSync(PAGE) ? readFileSync(PAGE, "utf8") : ""
const hasResultsHeading = pageSrc.includes('className="visually-hidden"')

describe.skipIf(!hasResultsHeading)("the results page uses the shared clamp", () => {
  it("quotes the query through clampQuery in both places", () => {
    expect(pageSrc.match(/clampQuery\(/g) ?? []).toHaveLength(2)
  })

  it("does not build the heading from the raw query", () => {
    expect(pageSrc).not.toMatch(/Results for \$\{query\.trim\(\)\}/)
  })

  it("has no second, inline cut of its own", () => {
    expect(pageSrc.match(/\.slice\(0,\s*44\)/g) ?? []).toHaveLength(0)
  })
})
