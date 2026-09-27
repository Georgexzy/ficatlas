import Link from "next/link"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "About & contact",   // layout.tsx appends " · FicAtlas"
  description:
    "What FicAtlas is, how it treats fanworks and AI, where its data comes from, and how to ask for a story to be taken down.",
  // The root layout deliberately sets no canonical (see the note there), so a
  // page without one has none at all — which is what Search Console means by
  // "duplicate without user-selected canonical".
  alternates: { canonical: "/about" },
}

// A public site needs a page that says what it is and how to reach a human.
// This one carries the takedown route as well, because an author who wants
// their work removed should not have to hunt for it — that is the single most
// important thing on this page for the person most likely to need it.
export default function About() {
  return (
    <div className="page-prose">
      {/* Was a lone "← Back to search" pointing at "/", which threw away
          whatever you had searched. The shared header instead: same one click
          home, plus Library and Settings, and its Search remembers your
          results. */}
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />

      <h1>About FicAtlas</h1>
      <p>
        FicAtlas is a search engine for fanfiction. It indexes work from{" "}
        <strong>Archive of Our Own</strong>, <strong>FanFiction.net</strong> and{" "}
        <strong>FictionAlley</strong> so you can search all three at once, rather than
        searching each one and missing the other two.
      </p>
      <p>
        It is an independent, non-commercial project. There is no advertising,
        no third-party tracking, and no data is sold.
      </p>

      <h2>Where the writing lives</h2>
      <p>
        For nearly everything in the index, FicAtlas stores{" "}
        <strong>only information about a story</strong> — its title, author,
        summary, tags, length and a link — and sends you to the original archive
        to read it. Authors keep their work, their comments and their kudos where
        they posted them.
      </p>
      <p>
        A small number of stories can be read here in full. These come from{" "}
        <strong>FictionAlley</strong>, an archive that closed, and were preserved
        so that they would not be lost. If you wrote one of them, see the section
        below.
      </p>

      {/* THE AUTHOR POLICY IS NOT WRITTEN HERE ANY MORE.
          It was, at length — a takedown section and a terms section, roughly
          forty lines — and the same four facts were also on /permissions and
          again on /takedown. Three statements of one policy in three voices,
          which is how /permissions came to be quoting "~19.9 million" against
          an index of 20.8M: nothing keeps three copies honest.

          /permissions is the single author page now. This one keeps the route
          to it, because an author who has found their work somewhere they did
          not put it must be able to reach the door from here without reading a
          policy first, and that is the most important link on this page for the
          person most likely to need it. */}
      <h2 id="takedown">For authors</h2>
      <p>
        If your work appears here and you would rather it did not, you can have
        it removed. No account, no explanation and no proof are required, and the
        text stops being readable as soon as you ask rather than after a review.
      </p>
      <p>
        You can also review everything held under your name, remove individual
        works, or set a standing preference that applies to your whole catalogue
        and to anything you publish later.
      </p>
      <p>
        <Link href="/permissions" className="card-btn card-btn--primary">
          Review and manage my work
        </Link>
      </p>
      <p className="page-prose__muted">
        Removal is immediate and needs no verification; granting permission —
        allowing FicAtlas to keep a complete copy of your text and serve it
        here — is the one action that does, because an unverified grant would be
        worth nothing to you. The full policy, and the form, are on that page.
      </p>

      {/* The page's own title has said "About & contact" since it was written,
          and until now the contact route was a sentence inside the takedown
          section — which went when that section was consolidated onto
          /permissions, leaving a page that promised contact details and gave
          none. Its own section now, so it cannot be lost to an edit elsewhere.

          The form is named first deliberately: it writes to the same queue an
          operator works, so it does not depend on an inbox being watched. */}
      <h2 id="contact">Contact</h2>
      <p>
        For anything concerning a specific work — removal, or setting your terms
        as its author — the{" "}
        <Link href="/permissions">author page</Link> is the fastest route, and
        it records the request directly rather than relying on mail being read.
      </p>
      <p>
        For anything else, including questions about this site or a problem with
        it, write to{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a>.
      </p>

      <h2>Source code</h2>
      <p>
        FicAtlas is open source. You can read every line, run your own copy, or
        send a fix:{" "}
        <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">
          github.com/Georgexzy/ficatlas
        </a>.
      </p>
      <p className="page-prose__muted">
        Licensed for non-commercial use: you may run your own copy, but you may
        not sell it or run it with advertising. That restriction is there for the
        authors whose work it indexes, who published for free on archives that
        undertook not to profit from them.
      </p>

      <h2 id="ai">AI and automated collection</h2>
      <p>
        Fanfiction archives have been scraped for model training without their
        authors&apos; consent, and readers are right to ask where any new index
        stands on it. FicAtlas&apos;s position:
      </p>
      <ul>
        <li>
          <strong>No generative model is applied to indexed work.</strong>{" "}
          Nothing in the index is passed to a model that writes, rewrites,
          summarises or continues stories. There is no chat interface over
          indexed text, no recommendations generated from full text, and no
          feature that reproduces or adapts an author&apos;s prose.
        </li>
        <li>
          <strong>The index is not a training corpus.</strong> FicAtlas does not
          publish bulk exports, does not license the index for machine learning,
          and does not supply works to AI companies. Known training crawlers are
          disallowed in <a href="/robots.txt">robots.txt</a>, the same position
          the OTW takes for AO3.
        </li>
        <li>
          <strong>Almost all of it is metadata and a link.</strong> For the great
          majority of works FicAtlas holds the title, author, summary, tags,
          length and a link to the original archive; the work itself remains
          where its author published it. Full text is limited to the preserved
          FictionAlley set described above, and any author may have that text
          removed immediately.
        </li>
        <li>
          <strong>AI assistance in building the software is not the same
          thing.</strong> Parts of FicAtlas&apos;s own code and search tooling
          were written with AI programming assistance, as much open-source
          software now is. No indexed work formed part of that, and no such tool
          is run against the index. The distinction is between writing a search
          engine and training on its contents.
        </li>
        <li>
          <strong>Collection is rate-limited and bounded.</strong> Metadata is
          gathered slowly, with backoff, and is taken from the Internet Archive
          wherever that avoids load on a live archive.
        </li>
      </ul>
      <p>
        If any of this is still not what you want for your work, the{" "}
        <Link href="/takedown">removal form</Link> asks nothing of you and takes
        effect immediately.
      </p>
    </div>
  )
}
