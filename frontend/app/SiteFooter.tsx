import Link from "next/link"

// Rendered on every page from the root layout. Deliberately small: it is not
// selling anything, it exists so that a stranger can tell what this site is and
// an author can find the takedown route without being told where to look.
export default function SiteFooter() {
  return (
    <footer className="site-footer">
      <span>FicAtlas — search AO3, FanFiction.net and FicAlley together</span>
      <span className="site-footer__sep">·</span>
      {/* The footer is on every page from the root layout, which makes this the
          one link that puts /fandoms — and through it every story page — inside
          the crawl graph. Without it the hubs are as unreachable as the story
          pages were, and the whole exercise achieves nothing.
          It earns its place for readers too: the search box needs you to know
          what you are looking for, and this is the answer to "what's in here?" */}
      <Link href="/fandoms">Browse fandoms</Link>
      <span className="site-footer__sep">·</span>
      <Link href="/about">About</Link>
      <span className="site-footer__sep">·</span>
      <Link href="/about#ai">AI policy</Link>
      <span className="site-footer__sep">·</span>
      {/* One author link, not two.
          "Remove my story" and "I'm an author" sat side by side pointing at the
          two halves of the same job, which is the duplication this footer was
          meant to avoid rather than create.
          Merged toward removal, not away from it. This link exists because an
          author must be able to find the takedown route without being told where
          to look, and that is the urgent case — so the label leads with
          "Remove", and the page it lands on opens with removal as a button
          before it mentions anything else. */}
      <Link href="/permissions">Remove or manage my work</Link>
      <span className="site-footer__sep">·</span>
      <a href="/robots.txt">Crawler policy</a>
      <span className="site-footer__sep">·</span>
      <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">Source</a>
    </footer>
  )
}
