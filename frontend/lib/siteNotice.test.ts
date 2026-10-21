import { afterEach, beforeEach, expect, it, vi } from "vitest"
import * as React from "react"
import { act, createElement } from "react"
import { createRoot, type Root } from "react-dom/client"
import SiteNotice from "../app/SiteNotice"

let root: Root
let host: HTMLDivElement

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-10-21T11:39:00Z"))
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  vi.stubGlobal("React", React)
  localStorage.clear()
  host = document.createElement("div")
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function returnNotice(start: string) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      site_notice: "FicAtlas will restart soon.",
      site_notice_start_at: start,
      site_notice_until: "2026-10-21T11:44:00Z",
    }),
  }))
}

it("shows an active maintenance notice from public settings", async () => {
  returnNotice("2026-10-21T11:38:00Z")
  await act(async () => root.render(createElement(SiteNotice)))
  expect(host.querySelector(".site-notice")?.textContent).toContain("FicAtlas will restart soon.")
})

it("shows a scheduled notice when its start time arrives", async () => {
  returnNotice("2026-10-21T11:39:30Z")
  await act(async () => root.render(createElement(SiteNotice)))
  expect(host.querySelector(".site-notice")).toBeNull()
  await act(async () => { vi.advanceTimersByTime(31_000) })
  expect(host.querySelector(".site-notice")?.textContent).toContain("FicAtlas will restart soon.")
})
