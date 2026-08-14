// Serialise an object for embedding inside a <script type="application/ld+json">
// block.
//
// The data is not ours: titles, summaries and author names are scraped from
// AO3/FFN and can contain anything, including "</script>", which inside a
// dangerouslySetInnerHTML script tag would terminate the block early and leave
// the rest to be parsed as HTML. JSON.stringify alone does not escape "<";
// JSON.parse of the escaped form still recovers the original string, so this
// is safe in both directions.
export function escapeJsonLd(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c")
}
