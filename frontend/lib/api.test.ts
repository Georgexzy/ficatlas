import { describe, expect, it } from "vitest"
import { buildSearchParams } from "./api"

describe("buildSearchParams", () => {
  it("keeps the original sentence as a ranking hint on interpreted result URLs", () => {
    const params = buildSearchParams({
      from: "first wizarding war Moody trains the Marauders",
      describe: "first wizarding war Moody trains the Marauders",
      characters: "Alastor Moody,Marauders (Harry Potter)",
    })
    expect(params.describe).toBe("first wizarding war Moody trains the Marauders")
    expect(params.characters).toBe("Alastor Moody,Marauders (Harry Potter)")
  })
})
