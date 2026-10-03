/** The offline fallback page must not tell a reader something untrue, and must
 *  look like the site.
 *
 * The service worker's last-resort page is very nearly unreachable, which is the
 * first thing worth recording. Navigations are cache-first and the handler
 * stores every response it fetches, without checking `res.ok` — so a 404 and a
 * 308 both get a cache entry. Measured on a fresh install: after the precache
 * there were exactly seven entries (`/`, `/account`, `/library`, `/login`,
 * `/settings` and the two story shells), and every URL I then requested offline
 * — a never-visited hub, `/follows`, a 404 path, and each of them again after
 * deleting every cache — was served without reaching this page. What is left is
 * a URL never requested on this device whose precache also failed.
 *
 * So this is not a surface to invest in. What it must still be is *honest*,
 * because the copy it used to carry was wrong twice over:
 *
 *   - "This page wasn't saved for offline use" borrowed the word the app uses
 *     for the deliberate ⤓ Save offline action. A page is never saved; it is
 *     cached as a side effect of being fetched, and unlike a saved story it is
 *     thrown away by the next build.
 *   - "Open it once while online, then it'll be available here" is true, and was
 *     worth checking rather than assuming — navigations are cache-first, so
 *     opening a page once genuinely does make it available. It was true for the
 *     wrong reason: I had assumed the worker cached only stories, which is what
 *     its own comment about the story shells invites you to assume.
 *
 * The palette assertions are the mechanical ones, and they exist because this
 * page had drifted by writing colours out by hand: #0e0e10 is not even the
 * site's default --bg (#111010) but a different theme's, #eee is not --text
 * (#ede9e0), and the link was an indigo against a gold accent that globals.css
 * deliberately never redefines. A worker cannot read localStorage, so
 * prefers-color-scheme is all it can honour — but honouring nothing meant a
 * light-mode reader got a black page.
 *
 * A source assertion rather than a render test: this suite deliberately does not
 * mount React (see vitest.config.ts), and the page is a string literal inside a
 * worker, so there is nothing to render in any case.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SW = readFileSync(
  path.resolve(__dirname, "../public/sw.template.js"), "utf8")
const CSS = readFileSync(
  path.resolve(__dirname, "../app/globals.css"), "utf8")

/** The literal the worker hands back when it has nothing else to serve. */
function fallbackPage(): string {
  const start = SW.indexOf("<!doctype html><meta charset=utf-8><title>Offline</title>")
  expect(start).toBeGreaterThan(-1)
  return SW.slice(start, SW.indexOf('status: 200', start))
}

/** The value of a token from globals.css' first (default dark) block. */
function token(name: string): string {
  const m = CSS.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{3,8})`))
  expect(m, `--${name} not found in globals.css`).toBeTruthy()
  return m![1].toLowerCase()
}

describe("service worker offline fallback", () => {
  it("uses the site's own palette rather than colours written by hand", () => {
    const page = fallbackPage()
    expect(page.toLowerCase()).toContain(`background:${token("bg")}`)
    expect(page.toLowerCase()).toContain(`color:${token("text")}`)
    // The accent is deliberately NOT redefined per theme — see globals.css — so
    // a link here should be the same gold as everywhere else on the site.
    expect(page.toLowerCase()).toContain(`color:${token("accent")}`)
  })

  it("honours a light-mode reader", () => {
    // A worker has no localStorage, so the stored theme is unreachable from here.
    // Hardcoding dark means every light-mode reader who sees this page is flashed
    // a black one, which is the only part of the site that ignores the theme.
    expect(fallbackPage()).toContain("prefers-color-scheme:light")
  })

  it("does not describe a cached page as a saved story", () => {
    // "Save offline" is a specific action on a story, stored in IndexedDB. Using
    // it of a cached page tells a reader the page was stored deliberately, which
    // is the mechanism that DOES survive a rebuild — the opposite of the truth.
    const page = fallbackPage()
    expect(page).toContain("isn't stored on this device")
    expect(page).not.toContain("wasn't saved for offline use")
  })

  it("says what expires, because a rebuild is what expires it", () => {
    // The one case where "open it once" stops being true, and it happens on every
    // deploy: the cache is named for the build and `activate` deletes the rest.
    expect(fallbackPage()).toMatch(/update of FicAtlas/)
  })

  it("points at the two things that really do work offline", () => {
    const page = fallbackPage()
    expect(page).toContain("href='/library'")
    expect(page).toContain("Save offline")
  })
})