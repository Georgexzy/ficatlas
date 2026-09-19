import { describe, it, expect } from "vitest"
import { adminTabFor } from "./adminTabs"

describe("adminTabFor", () => {
  it("resolves the names the tabs have now", () => {
    expect(adminTabFor("index")).toBe("index")
    expect(adminTabFor("audience")).toBe("audience")
    expect(adminTabFor("outreach")).toBe("outreach")
    expect(adminTabFor("moderation")).toBe("moderation")
  })

  it("still resolves the names they had before the regrouping", () => {
    // Every one of these is reachable from outside this repo: /takedowns
    // redirects carrying one, Settings linked another, and an operator may
    // have bookmarked any of them. A rename that breaks its own inbound links
    // is a rename that gets reverted.
    expect(adminTabFor("takedowns")).toBe("moderation")
    expect(adminTabFor("traffic")).toBe("audience")
    expect(adminTabFor("health")).toBe("index")
    expect(adminTabFor("queue")).toBe("outreach")
  })

  it("leaves the default alone for nothing, or for a name it does not know", () => {
    expect(adminTabFor(null)).toBeUndefined()
    expect(adminTabFor("")).toBeUndefined()
    expect(adminTabFor("nonsense")).toBeUndefined()
  })

  it("is not case- or whitespace-sensitive", () => {
    expect(adminTabFor(" Takedowns ")).toBe("moderation")
  })
})
