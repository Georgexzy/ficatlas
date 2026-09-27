import Link from "next/link"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "Privacy",   // layout.tsx appends " · FicAtlas"
  description:
    "What FicAtlas records, what it deliberately cannot know, how long it "
    + "keeps anything, and how to have your account and data deleted.",
  alternates: { canonical: "/privacy" },
}

// Required before Google will publish an OAuth consent screen, and worth
// having on its own terms: a site that asks people to make an account owes
// them a plain account of what it stores.
//
// Written from the code rather than from a template. Every claim below is
// checkable in this repository — the visitor hash in backend/tracking.py, the
// retention sweep beside it, the session cookie in backend/api/auth.py — and
// that is the point. A privacy policy describing something the software does
// not do is worse than none, because it is a promise nobody is keeping.
//
// ON REGISTER. This was rewritten for tone, having first been written in the
// same conversational voice as the rest of the site: "run by one person",
// "deliberately weak", "that is a deliberate trade", "it will be honoured".
// The facts were right and the register was wrong. This is the document
// somebody consults when they are already uneasy, and the one Google reads
// before publishing a consent screen, so it has to read as though it were
// written to be relied on.
//
// What was deliberately NOT changed is the plainness. Replacing "the IP address
// itself is never stored" with "IP addresses are not retained" would be more
// formal and less clear, and clarity is the substance here — a policy nobody
// can follow protects nobody.
//
// The date is maintained BY HAND. It is a claim about when a person last
// checked this against the code, which nothing can derive from a build; a date
// that moved on every deploy would assert the opposite of what it appears to.
const LAST_UPDATED = "27 September 2026"

export default function Privacy() {
  return (
    <div className="page-prose">
      <SiteHeader />
      <h1>Privacy</h1>

      <p className="page-prose__muted">Last updated {LAST_UPDATED}.</p>

      <p>
        FicAtlas is a search engine for fanfiction. It is independently run and
        sells nothing, so it has no commercial reason to identify the people who
        use it, and the parts of it that count visitors are built so that it
        largely cannot. This page sets out what is collected, why, how long it is
        kept, and how to have it removed.
      </p>
      <p>
        Every statement below describes how the software behaves in the version
        currently deployed. The source code is public, and each mechanism
        described here can be read in it.
      </p>

      <h2>Visitors without an account</h2>
      <p>
        No information that identifies you is stored. When a page is loaded or a
        search is run, the server records the page or query, the time, whether a
        result was followed through to an archive, and a <em>visitor hash</em>.
      </p>
      <p>
        That hash is the only means by which visitors are counted, and it is
        limited by design. It is a keyed hash of the current{" "}
        <strong>date</strong>, the IP address of the request, and the
        browser&rsquo;s user-agent string. Because the date forms part of it, the
        same person visiting on two days produces two unrelated hashes that
        cannot be linked to one another. The IP address itself is never stored.
      </p>
      <p>
        The consequence is that this site can report how many people searched for
        a given term yesterday, but cannot establish whether any of them returned
        today. That is an intentional limitation: the ability to measure
        returning readers is given up in exchange for not accumulating a profile
        of any individual.
      </p>
      <p>
        Search queries are recorded, as they are the principal measure of whether
        the index is working. They are stored against the hash described above,
        and against no name, address or IP address.
      </p>

      <h2>Retention</h2>
      <p>
        Traffic records are deleted automatically after ninety days. No archive
        of older traffic is kept.
      </p>

      <h2>Account holders</h2>
      {/* "and only this" makes this list a PROMISE, so it has to be complete,
          and the first version was not. It said an email address was used "for
          signing in" (you sign in with a username; the address is optional and
          only ever used for a reset), described follows as "fandoms and
          pairings" (you follow WORKS), and omitted session records — which
          include the browser string of each device — along with everything the
          reader's own data actually covers.

          Checked against the schema rather than written from memory:
          models/user.py for User and UserSession, api/userdata.py ALLOWED_KEYS
          and lib/storageKeys.ts for the synced data, api/follows.py for follows.
          Re-check it against those four whenever any of them changes: an
          incomplete list under "and only this" is not a rough edge in a privacy
          page, it is a false statement in the one document that exists to be
          exact. */}
      <p>If you create an account, the following is stored, and only this:</p>
      <ul>
        <li>the username you choose, and the date the account was created;</li>
        <li>
          an email address, <em>only if you provide one</em>. It is optional;
          sign-in uses your username, and the address is used solely to send a
          password reset at your request;
        </li>
        <li>
          a password <em>hash</em>, never the password itself, so that it cannot
          be read or recovered by anyone operating this site. An account created
          through Google sign-in has no password unless one is added later;
        </li>
        <li>
          where Google sign-in is used, the account identifier Google returns.
          Google discloses your email address to this site and nothing further —
          not your contacts, your files, or any other part of your Google
          account;
        </li>
        <li>whether the account is a reader, an administrator or the owner;</li>
        <li>
          one record for each signed-in device, so that you can review them and
          sign them out from your account settings. Each holds the time of
          sign-in, the time of last use, the expiry time, and the
          browser-and-platform description the browser itself supplies (for
          example, &ldquo;Chrome on Android&rdquo;). No IP address is recorded;
        </li>
        <li>
          the reading data the account accumulates: the works you follow, your
          bookmarks, your position in each story, the searches you have saved and
          those you have run recently, the works kept for offline reading, and
          your reader and search preferences;
        </li>
        <li>
          {/* Small, and listed because the sentence above says "and only this".
              It is stored per account rather than per device so that declining
              once is not re-asked on every device you sign in from. */}
          whether you have declined the prompt asking you to add an email
          address, so that it is not shown again.
        </li>
      </ul>
      <p>
        The final item includes the never-show-me list — the pairings, tropes,
        fandoms, characters and authors you have excluded from every search. It
        is identified separately because it is the most revealing record this
        site holds: a standing list of what a person declines to read discloses
        more than any individual search does. It is stored so that it applies
        across your devices, it is used for no purpose other than filtering your
        own results, it is disclosed to no one, and it is removed when the
        account is removed.
      </p>
      <p>
        One cookie is set, which holds your session so that you remain signed in.
        No advertising or tracking cookies are set, as the site carries no
        advertising.
      </p>

      <h2>Your rights over this data</h2>
      {/* Every right named here is exercisable through a control that already
          exists, and each names the control. A policy that lists rights without
          saying where to exercise them is the shape that teaches people to
          distrust policies. */}
      <p>
        Each of the following can be exercised directly, without making a request
        to anyone:
      </p>
      <ul>
        <li>
          <strong>Access.</strong> Your account settings show each category
          above: the Account tab lists your username, address, role and every
          signed-in device; the Your data tab lists the reading data, how much
          of it there is, and exports it as a single file. (The export covers
          the reading data, which is the part that is not already shown on
          screen.)
        </li>
        <li>
          <strong>Correction.</strong> Your email address, your password and
          every preference can be changed from your account settings. A username
          cannot currently be changed after the account is created; if you need
          a different one, write to the address below.
        </li>
        <li>
          <strong>Erasure.</strong> Deleting the account removes it and
          everything stored against it, as set out below. Individual categories
          can also be cleared separately, from the same page, without deleting
          the account.
        </li>
        <li>
          <strong>Using the site without any of this.</strong> No account is
          required in order to search or to read. Without one, the only records
          are the anonymous traffic records described above, which identify
          nobody and are deleted after ninety days.
        </li>
      </ul>
      <p>
        If any of these controls does not work, write to{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> and the request
        will be carried out manually.
      </p>

      <h2>Third-party services</h2>
      <ul>
        <li>
          <strong>Cloudflare</strong> serves the site and therefore handles
          requests in transit, as any hosting provider does.
        </li>
        <li>
          <strong>Google Fonts</strong> serves two typefaces, which your browser
          retrieves from Google.
        </li>
        <li>
          <strong>Google</strong>, only where you choose to sign in with it.
        </li>
      </ul>
      <p>
        There is no analytics provider and no advertising network, and no data is
        sold or shared with any other party. The visitor counting described above
        is performed by this site, on its own server.
      </p>

      <h2>Indexed fanworks</h2>
      <p>
        FicAtlas indexes metadata describing works published on other archives —
        titles, summaries, tags, and links back to the archive that hosts each
        one. It is not the publisher of those works. Authors may have their work
        removed from the index, and may set standing terms governing what
        FicAtlas does with it; both are available on the{" "}
        <Link href="/permissions">author page</Link>, and neither requires
        verification or an explanation.
      </p>

      <h2>Deleting your account</h2>
      <p>
        An account can be deleted from{" "}
        <Link href="/settings?tab=account">your account settings</Link>, which
        removes the account and everything stored against it. If that control
        does not work, write to{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> and it will be
        done manually.
      </p>

      <h2>Changes to this page</h2>
      <p>
        If what is collected changes, this page is revised to match and the date
        at the top is updated. It describes the behaviour of the software as
        deployed, and is checked against the source each time it is revised.
      </p>
    </div>
  )
}
