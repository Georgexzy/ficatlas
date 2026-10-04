import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { announceGoHome, GO_HOME_EVENT } from "./goHome"

function heard(url: string): boolean {
  window.history.replaceState(null, "", url)
  let got = false
  const h = () => { got = true }
  window.addEventListener(GO_HOME_EVENT, h)
  announceGoHome()
  window.removeEventListener(GO_HOME_EVENT, h)
  return got
}

describe("announceGoHome", () => {
  it("fires only where a link to / cannot navigate: on bare /", () => {
    // "Surprise me" shows results with the address still at "/", so a link to
    // "/" was a link to the current page and the wordmark did nothing.
    expect(heard("/")).toBe(true)
    expect(heard("/?q=drarry")).toBe(false)
    expect(heard("/library")).toBe(false)
  })

  it("is wired to the wordmark and the Home tab, and heard by the search page", () => {
    const header = readFileSync(path.resolve(__dirname, "../app/SiteHeader.tsx"), "utf8")
    const page = readFileSync(path.resolve(__dirname, "../app/page.tsx"), "utf8")
    expect(header.match(/onClick=\{announceGoHome\}/g)?.length).toBe(2)
    expect(page).toContain("window.addEventListener(GO_HOME_EVENT, reset)")
  })
})
