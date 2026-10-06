/** A hub page must name its own canonical even when its data lookup fails.
 *
 * generateMetadata used to `return {}` when the hub could not be loaded, while
 * the page body looked the hub up again and rendered fine. Google then received
 * a working page with the HOME page's title and no canonical, and Search
 * Console filed hubs crawled during a slow spell (5-7 Sept 2026) as "duplicate
 * without user-selected canonical". The canonical comes from the URL's own
 * slug, so it never depends on the lookup.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const pages = ["fandom/[slug]/page.tsx", "ship/[slug]/page.tsx", "fandom/[slug]/[topic]/page.tsx"]

describe("hub metadata on a failed lookup", () => {
  for (const p of pages) {
    const src = readFileSync(path.resolve(__dirname, "../app", p), "utf8")
    const meta = src.slice(src.indexOf("export async function generateMetadata"))
                    .split("\nexport default")[0]
    it(`${p} never returns empty metadata`, () => {
      expect(meta).not.toMatch(/return \{\}/)
    })
    it(`${p} sets a canonical on the failure path`, () => {
      const failure = meta.slice(0, meta.indexOf("return {", meta.indexOf("if (") ) + 400)
      expect(failure).toMatch(/canonical/)
    })
  }
})
