/** Guards on the front door's activity strip, in the same spirit as
 *  followAffordance.test.ts: the two properties below are the ones a
 *  reasonable-looking change would undo, and neither can be caught by a unit
 *  test of a function because both are conditions in JSX.
 *
 *  What is being held shut:
 *
 *  1. The picked-fandoms row must not be gated on having READ anything. Its
 *     whole audience is the reader who chose fandoms at signup and has done
 *     nothing else yet — requiring progress would show it to exactly nobody
 *     new, and "only show this when there is activity" is the argument a
 *     reviewer would naturally reach for.
 *
 *  2. The follow nudge must ask whether the reader FOLLOWS anything, not
 *     whether they have unread updates. Those are different questions: zero
 *     unread is also what a reader following ten finished works looks like,
 *     and offering that person a follow reads as the site not noticing.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SRC = readFileSync(path.resolve(__dirname, "../app/page.tsx"), "utf8")

/** The component, bounded by the next one rather than by a line count — the
 *  same technique followAffordance uses, for the same reason. */
function activityBlock(): string {
  const a = SRC.indexOf("function ReadingActivity()")
  const b = SRC.indexOf("function EmptyState", a)
  expect(a).toBeGreaterThan(-1)
  expect(b).toBeGreaterThan(a)
  return SRC.slice(a, b)
}

describe("the home page's activity strip", () => {
  it("shows picked fandoms to a reader who has read nothing", () => {
    const block = activityBlock()
    const gate = block.slice(block.indexOf("if (!hasFollow"))
    expect(gate).toContain("yourFandoms.length === 0")
  })

  it("offers the follow only to a reader who follows nothing at all", () => {
    expect(activityBlock()).toMatch(/\(data\.follows \?\? 0\) === 0 && nextUp/)
  })

  it("never offers to follow a work that has finished", () => {
    expect(activityBlock()).toMatch(/cr\.find\(s => s\.status === "in_progress"\)/)
  })

  it("links each picked fandom to its hub page", () => {
    // The hub page is the one route into the index a crawler may follow, and
    // the destination the browse rows below already use.
    expect(activityBlock()).toContain("href={`/fandom/${f.slug}`}")
  })
})
