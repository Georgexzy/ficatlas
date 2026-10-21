/** A broader FF.net search for a pairing with no source-supplied ship tag. */
export function ffnetNameSearchHref(pairing: string): string | null {
  const halves = pairing.split("/")
  if (halves.length !== 2) return null
  const names = halves.map(half => half.trim().split(/\s+/)[0])
  if (names.some(word => !word || word.length < 3)) return null
  return `/?q=${encodeURIComponent(names.join(" "))}&sites=ffnet`
}
