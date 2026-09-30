/** The follow control must be visible to a reader who is not signed in.
 *
 * Following a WIP across three archives is the one thing no archive can do and
 * the headline reason to make an account. Measured 2026-09-30: the `follows`
 * table had ZERO rows, ever.
 *
 * It was not a broken button. It was arithmetic. Following was offered only to
 * a signed-in reader, and only on cards -- and over fourteen days 327 visitors
 * produced 62 who opened a story at all. The feature sat behind the step most
 * people never take, so nobody could want it.
 *
 * The STORY PAGE had already reached this conclusion and reversed itself (see
 * the comment there beginning "FOLLOW IS SHOWN TO EVERYONE"). That reversal
 * never reached the result card. This file is what stops it being undone
 * again, because the argument for removing it is genuinely reasonable-sounding
 * -- "a button whose only outcome is a 401" -- and is the exact reasoning that
 * had to be abandoned twice.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SRC = readFileSync(path.resolve(__dirname, "../app/page.tsx"), "utf8")

const SIGNED_OUT = '{!user && story.status === "in_progress" && ('
const SIGNED_IN = '{user && story.status === "in_progress" && ('

/** Just the signed-out branch. Bounded by the signed-in branch that follows
 *  it, because a fixed-length window runs into that one -- which legitimately
 *  IS a <button> -- and the assertions below are about this branch only. */
function signedOutBlock(): string {
  const a = SRC.indexOf(SIGNED_OUT)
  const b = SRC.indexOf(SIGNED_IN, a + 1)
  expect(a).toBeGreaterThan(-1)
  expect(b).toBeGreaterThan(a)
  return SRC.slice(a, b)
}

describe("follow on a result card", () => {
  it("is offered to a signed-out reader", () => {
    expect(SRC).toMatch(/\{!user && story\.status === "in_progress" && \(/)
  })

  it("is a link to sign in, not a dead button", () => {
    const block = signedOutBlock()
    expect(block).toContain('href="/login"')
    expect(block).not.toContain("<button")
  })

  it("comes back to the search the reader was looking at", () => {
    const block = signedOutBlock()
    expect(block).toContain("window.location.search")
    expect(block).toContain("next=")
  })

  it("reads location at click time, not during render", () => {
    /** This component server-renders; touching `window.location` in the body
     *  is a hydration mismatch, and the bare href has to stay valid without
     *  JS. */
    const block = signedOutBlock()
    const iClick = block.indexOf("onClick")
    const iLoc = block.indexOf("window.location")
    expect(iClick).toBeGreaterThan(-1)
    expect(iLoc).toBeGreaterThan(iClick)
  })

  it("stays limited to unfinished works", () => {
    /** There is nothing to be told about a work that is complete, and a
     *  control on every card would be noise rather than an offer. */
    const signedOut = SRC.indexOf(SIGNED_OUT)
    const signedIn = SRC.indexOf(SIGNED_IN, signedOut + 1)
    expect(signedOut).toBeGreaterThan(-1)
    expect(signedIn).toBeGreaterThan(-1)
  })
})
