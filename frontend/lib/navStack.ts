// Where the reader actually came from, which the browser will not tell you.
//
// `router.back()` walks the browser's history one entry, and the site pushes
// entries that are not where anyone wants to return to. Leaving the reader is
// the clearest case: exiting a chapter pushes the story's detail page, so the
// stack reads
//
//     /  →  /story/X  →  /story/X/chapter/1  →  /story/X
//
// and Back from that last page goes into the chapter you just closed. Press it
// twice and you are back on the detail page again. The story is a loop with no
// way out but the header.
//
// document.referrer is empty for same-origin client transitions, the Navigation
// API is not in Safari, and history entries cannot be inspected — so the only
// way to know what is behind you is to have written it down. This keeps a short
// list of the paths this tab has visited, in sessionStorage so it dies with the
// tab and never leaks between tabs or profiles.

const KEY = "ficatlas:navstack"
const LIMIT = 25

function read(): string[] {
  if (typeof sessionStorage === "undefined") return []
  try {
    const raw = sessionStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v.filter(x => typeof x === "string") : []
  } catch {
    return []
  }
}

function write(stack: string[]): void {
  if (typeof sessionStorage === "undefined") return
  try {
    sessionStorage.setItem(KEY, JSON.stringify(stack.slice(-LIMIT)))
  } catch {
    // Private mode, or a full quota. Navigation still works, it just stops
    // being clever — which is the correct thing to degrade to.
  }
}

/** Record a visit. Safe to call on every render of a page; repeats are ignored. */
export function recordPath(path: string): void {
  const stack = read()
  if (stack[stack.length - 1] === path) return   // re-render, not a navigation
  stack.push(path)
  write(stack)
}

/** True when `candidate` is the same page as `path`, or somewhere inside it.
 *
 *  `/story/X/chapter/3` is inside `/story/X`. Going "back" from a story to one
 *  of its own chapters is the loop this whole module exists to break, and it is
 *  the same mistake whether the chapter is 1 or 40.
 */
export function isWithin(candidate: string, path: string): boolean {
  const c = candidate.split("?")[0].replace(/\/+$/, "")
  const p = path.split("?")[0].replace(/\/+$/, "")
  return c === p || c.startsWith(p + "/")
}

/** The nearest previous path that is not the current page or a child of it.
 *
 *  Returns null when there is nothing sensible behind — a tab opened straight
 *  onto this URL, or a session spent entirely inside one story. The caller then
 *  uses its named fallback, which is honest about where it is going instead of
 *  appearing to go "back" to somewhere you have never been.
 */
export function previousOutside(current: string): string | null {
  const stack = read()
  // Skip the current page itself wherever it sits at the top, then keep walking
  // back over anything inside it.
  for (let i = stack.length - 1; i >= 0; i--) {
    if (!isWithin(stack[i], current)) return stack[i]
  }
  return null
}

/** Drop everything from `path` (inclusive) onwards.
 *
 *  Called when a back-navigation actually happens, so the stack matches where
 *  the reader now is rather than growing forever with places they have left.
 */
export function truncateTo(path: string): void {
  const stack = read()
  const i = stack.lastIndexOf(path)
  write(i >= 0 ? stack.slice(0, i + 1) : stack)
}
