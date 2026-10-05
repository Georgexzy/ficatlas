// The name to SHOW for a fandom on a tile, where the full AO3 name stays the
// page's title and the link's tooltip.
//
// AO3 files many fandoms under every script they are known in:
// "原神 | Genshin Impact (Video Game)", "僕のヒーローアカデミア | Boku no Hero
// Academia | My Hero Academia". On a 170px tile that is cut to "原神 | Gensh…",
// which names nothing for a reader of an English-language page. The last
// segment is the one AO3 gives in English by convention, so that is shown,
// with its disambiguator kept: "(Anime & Manga)" is what tells two otherwise
// identical tiles apart.
export function shortHubName(name: string): string {
  const parts = (name || "").split(" | ").map(s => s.trim()).filter(Boolean)
  return parts.length > 1 ? parts[parts.length - 1] : (name || "").trim()
}
