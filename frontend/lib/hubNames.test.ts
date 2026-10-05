import { describe, it, expect } from "vitest"
import { shortHubName } from "./hubNames"

describe("shortHubName", () => {
  it("shows the English half of a multi-script AO3 name", () => {
    expect(shortHubName("原神 | Genshin Impact (Video Game)")).toBe("Genshin Impact (Video Game)")
    expect(shortHubName("僕のヒーローアカデミア | Boku no Hero Academia | My Hero Academia"))
      .toBe("My Hero Academia")
  })
  it("leaves an ordinary name and a pairing alone", () => {
    expect(shortHubName("Harry Potter")).toBe("Harry Potter")
    expect(shortHubName("Castiel/Dean Winchester")).toBe("Castiel/Dean Winchester")
  })
})
