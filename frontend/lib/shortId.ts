// Short, stateless story links.
//
// A story's address is a UUID — 36 characters of hex and hyphens — so a link
// you paste into Discord or a DM is 55 characters of which 36 say nothing to a
// human. ha.mr (github.com/p2r3/ha.mr) solves the general version of this by
// Huffman-coding arbitrary URLs client-side with no backend; the useful idea to
// borrow is that the mapping is DETERMINISTIC and REVERSIBLE, so nothing has to
// be stored and no lookup table can drift or fill up.
//
// Here that is simpler than Huffman, because the thing being shortened is not
// an arbitrary URL: it is 128 bits already. A UUID's hyphens are formatting and
// its hex is 4 bits per character, so base64url carries the same 128 bits in 22
// characters instead of 36 — a 39% cut with nothing stored and nothing lost.
//
//     /story/4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c   43 chars
//     /s/SxX-flGqRsa47PBzjI57PA                     25 chars
//
// Base64URL, not base62: base62 needs bignum division for 128 bits, while
// base64url is a byte-aligned transform the platform already has. It is two
// characters longer than base62 would be (22 vs 22 — identical here, in fact)
// and considerably harder to get wrong.
//
// `/s/<code>` is a redirect to the canonical `/story/<uuid>`, never a page in
// its own right, so there is exactly one indexable URL per work and short links
// cost nothing in duplicate content.

const HEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** 22-char base64url for a UUID, or null if it isn't one. */
export function encodeStoryId(uuid: string): string | null {
  if (!HEX.test(uuid.trim())) return null
  const hex = uuid.replace(/-/g, "")
  const bytes = new Uint8Array(16)
  for (let i = 0; i < 16; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  let bin = ""
  for (const b of bytes) bin += String.fromCharCode(b)
  // btoa exists on the server in Node 16+ as well as in the browser.
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

/** The UUID a short code stands for, or null if the code is malformed. */
export function decodeStoryId(code: string): string | null {
  const c = code.trim()
  // 22 base64url characters is exactly 16 bytes. Anything else is not one of
  // ours, and decoding it would produce a plausible-looking wrong UUID.
  if (!/^[A-Za-z0-9_-]{22}$/.test(c)) return null
  try {
    const bin = atob(c.replace(/-/g, "+").replace(/_/g, "/") + "==")
    if (bin.length < 16) return null
    const hex = Array.from(bin.slice(0, 16), ch =>
      ch.charCodeAt(0).toString(16).padStart(2, "0")).join("")
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
            hex.slice(16, 20), hex.slice(20, 32)].join("-")
  } catch {
    return null
  }
}

/** The short path for a story, falling back to the canonical one. */
export function shortStoryPath(uuid: string): string {
  const code = encodeStoryId(uuid)
  return code ? `/s/${code}` : `/story/${uuid}`
}
