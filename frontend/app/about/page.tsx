import Link from "next/link"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "About & contact",   // layout.tsx appends " · FicAtlas"
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
        <strong>FictionAlley</strong> so you can search all three at once, rather than
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
        <strong>FictionAlley</strong>, an archive that shut down, and were preserved
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
        If you want the listing removed as well, there is a box on the form for
        that. There is no contact address yet — this runs on a home machine and
        has no domain of its own — so the form is the way to reach whoever
        maintains it.
      </p>

      {/* Placed immediately after the takedown section on purpose. An author
          reading this page is usually here because they found their work and
          want it gone; the other options only make sense once they know that
          door is open and costs them nothing. */}
      <h2 id="authors">If you would rather set your own terms</h2>
      <p>
        Removal is not the only option. You can see everything FicAtlas holds
        under your name, take down individual works, or set a standing choice
        that applies to your whole back catalogue{" "}
        <strong>and anything you post later</strong> — so you only say it once.
      </p>
      <p>
        The choice that matters most is whether FicAtlas may keep{" "}
        <strong>a complete copy of your stories&apos; text</strong> on this
        server and let people read it here, rather than only listing the work and
        linking you to the archive. That is the one thing worth being asked
        about, and the one thing this site will not do on the strength of an
        unverified form.
      </p>
      <p>
        <strong>None of that needs proof</strong>, with one exception. Saying{" "}
        <em>no</em> — don&apos;t store my text, don&apos;t index me — is taken at
        face value, because a request that only ever removes permission cannot be
        used to take anything from anyone. Saying <em>yes</em> is different:
        anyone can type an author&apos;s name into a form, so permission to host
        your work is only recorded once you have shown you control the account it
        was posted from, by putting a one-time code in your own profile.
      </p>
      {/* One button. These were two — "See what is held under my name" and "Set
          my terms" — pointing at the two author pages that have since become
          one, so they now lead to the same place. And the first went via
          /permissions/manage, which is only a redirect now: our own navigation
          should not bounce through one, those exist for bookmarks and inbound
          links. */}
      <p>
        <Link href="/permissions" className="card-btn card-btn--primary">
          See my work and set my terms
        </Link>
      </p>
      <p className="page-prose__muted">
        Verifying works for Archive of Our Own only. FanFiction.net blocks
        automated requests outright, so their profiles cannot be read to check a
        code — FF.net authors can still restrict and remove, just not grant.
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
        <Link href="/takedown">takedown form</Link> — it reaches whoever
        maintains this, and it asks nothing of you. Authors come first.
      </p>
    </div>
  )
}
