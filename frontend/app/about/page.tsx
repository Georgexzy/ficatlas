import Link from "next/link"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "About & contact — FicAtlas",
  description:
    "What FicAtlas is, how it treats fanworks and AI, where its data comes from, and how to ask for a story to be taken down.",
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
        <strong>FicAlley</strong> so you can search all three at once, rather than
        searching each one and missing the other two.
      </p>
      <p>
        It is a personal, non-commercial project. There are no adverts, no
        trackers, and nothing is sold.
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
        A small number of stories can be read here directly. These come from{" "}
        <strong>FicAlley</strong>, an archive that shut down, and were preserved
        so they would not disappear. If you wrote one of them, the section below
        is for you.
      </p>

      <h2 id="takedown">Asking for a story to be removed</h2>
      <p>
        If you are the author of a story whose text can be read on FicAtlas and
        you would rather it were not, you can have it removed. You do not need to
        explain yourself, and you do not need to prove anything first.
      </p>
      <p>
        <strong>The text comes down straight away</strong>, as soon as the form is
        submitted — not after a review. The story stays listed as a title, author
        and link, so people can still find your work where you publish it now.
      </p>
      <p>
        <Link href="/takedown" className="card-btn card-btn--primary">
          Request a takedown
        </Link>
      </p>
      <p className="page-prose__muted">
        You will not be asked to prove that the work is yours. Fandom runs on pen
        names, and most of what is hosted here came from an archive that no
        longer exists, so there is nothing to prove it against. Nothing is
        deleted either — the text stops being readable, and stays recoverable in
        case a request was mistaken.
      </p>
      <p className="page-prose__muted">
        If you would rather write to a person, or you want the listing removed as
        well, email <strong>admin@ficatlas.app</strong> and say so.
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
        Licensed for non-commercial use. You are welcome to run it for yourself
        or your corner of fandom; you may not sell it or run it with adverts.
        That restriction exists for the authors whose work it indexes, not for
        me — they published for free, on archives that promised not to profit
        from them.
      </p>

      <h2 id="ai">AI, crawling, and your work</h2>
      <p>
        Fandom has good reasons to be wary of anything that looks like another
        scrape of AO3 for someone else&apos;s model. So here is what FicAtlas is,
        and what it is not, stated plainly.
      </p>
      <ul>
        <li>
          <strong>No generative AI acts on fanfiction here.</strong> Nothing in
          the index is fed to a model that writes, rewrites, summarises, or
          &ldquo;continues&rdquo; stories. There is no chatbot over your fic, no
          auto-generated recommendations trained on full text, and no feature
          that remixes someone else&apos;s prose.
        </li>
        <li>
          <strong>This is not a training dataset.</strong> FicAtlas does not
          publish bulk dumps of works, does not sell access to the index for
          machine learning, and does not hand stories to AI companies. Training
          crawlers are refused in{" "}
          <a href="/robots.txt">robots.txt</a> — the same stance the OTW takes
          for AO3.
        </li>
        <li>
          <strong>Almost everything is metadata and a link.</strong> For the
          vast majority of works we store title, author, summary, tags, length
          and where to read it on the original archive. The story itself stays
          where you posted it. Full text here is limited to a small preserved
          set from a dead archive (see above), and authors can have that text
          taken down immediately.
        </li>
        <li>
          <strong>Building the site is not the same as mining fic.</strong>{" "}
          Parts of FicAtlas&apos;s own code and search tooling were written with
          ordinary programming help — the way many open-source projects are
          built. That help never trained on, and never runs against, the
          fanworks in the index. The stories are data for a search engine, not
          fuel for a model.
        </li>
        <li>
          <strong>Collection is slow and bounded.</strong> Metadata is gathered
          politely, with rate limits and backoff, and from the Internet Archive
          wherever that keeps load off live archives. It is indexing for
          readers, not harvesting corpora.
        </li>
      </ul>
      <p>
        If something here still feels wrong for your work, use the{" "}
        <Link href="/takedown">takedown form</Link> or write to{" "}
        <strong>admin@ficatlas.app</strong>. Authors come first.
      </p>
    </div>
  )
}
