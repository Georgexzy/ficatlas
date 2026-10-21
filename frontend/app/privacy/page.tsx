import Link from "next/link"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "Privacy",
  description: "How FicAtlas handles account data, reading data, traffic records, cookies and requests to delete your data.",
  alternates: { canonical: "/privacy" },
}

const LAST_UPDATED = "21 October 2026"

export default function Privacy() {
  return (
    <main id="main" className="page-prose">
      <SiteHeader />
      <h1>Privacy</h1>
      <p className="page-prose__muted">Last updated {LAST_UPDATED}.</p>
      <p>
        FicAtlas is an independent, non-commercial fanfiction search and reading
        site. This page explains what information the site uses to run accounts,
        provide library features, measure traffic and respond to author requests.
        Contact <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> about
        your information or this notice.
      </p>

      <h2>Traffic and server logs</h2>
      <p>
        FicAtlas records page views, searches and result clicks to understand
        which parts of the site work. These records can include a page path,
        search terms, result counts, the referring site&apos;s hostname, time,
        response status and performance measurements. Automated traffic is
        filtered from the reader figures where it can be identified. Browser
        Do Not Track settings are respected for page-view reporting.
      </p>
      <p>
        To count visits within a day, the application creates a short, keyed
        hash from the date, request IP address and browser user-agent. The
        traffic table stores the hash, not the IP address or full user-agent;
        hashes from different days are not designed to be linked. The browser
        also stores the date of its last visit locally and may report whether
        this is a first or returning visit. These measures estimate readership;
        they do not establish a person&apos;s identity.
      </p>
      <p>
        The web server separately writes operational access logs containing
        request addresses, IP addresses, referrers and user-agent strings. These
        logs are used to keep the service running and investigate abuse, and are
        rotated by file size. Cloudflare also processes requests as the site&apos;s
        network provider. The application&apos;s traffic records are automatically
        deleted after 90 days; this period does not describe server logs or
        database backups.
      </p>

      <h2>Accounts and libraries</h2>
      <p>
        An account stores your username, account dates and role, a password hash
        if you use a password, and an email address if you provide one. Email is
        used for a requested password reset and for followed-work updates only
        if you enable them. Google sign-in uses your Google account identifier
        and verified email address; FicAtlas requests Google&apos;s basic profile
        scope but does not store your contacts, files or Google password.
      </p>
      <p>
        Signed-in device records include sign-in, last-use and expiry times and
        the browser description. Your account can also hold bookmarks, reading
        progress, recent and saved searches, followed works, preferences and
        exclusions. The works you save for offline use are stored in your
        browser; library information may sync to your account across devices.
        If you import a work into your private library, FicAtlas stores a copy
        of its text and an access record for your account. Other readers cannot
        browse that private copy.
      </p>
      <p>
        A private import may share a stored story copy with another reader who
        independently imports the same work. Deleting an account removes that
        account&apos;s access record and synced reading data; the shared story
        copy may remain if another reader has access to it, or if the work is
        part of the public index.
      </p>

      <h2>Cookies and browser storage</h2>
      <p>
        FicAtlas uses a session cookie to keep you signed in. Google sign-in
        temporarily uses a separate cookie to protect the sign-in process. A
        browser-check cookie can be set when you open a direct search link, to
        distinguish a browser from automated requests. The site also uses
        local storage, session storage and IndexedDB for preferences, navigation,
        saved reading and offline copies. There are no advertising cookies or
        third-party analytics scripts.
      </p>

      <h2>Author requests</h2>
      <p>
        If you submit a <Link href="/takedown">removal request</Link>, FicAtlas
        records the story address, name, email, relationship to the work, any
        message and the request IP address so the request can be reviewed and
        abuse investigated. A <Link href="/permissions">permission request</Link>
        may also record your archive name, choice, verification evidence and
        optional contact email. These records are separate from reader accounts.
      </p>

      <h2>Other services</h2>
      <p>
        Cloudflare carries site traffic. Google serves web fonts requested by
        the browser, and handles sign-in only if you choose that option. FicAtlas
        does not sell visitor or account data and does not use an advertising
        network or third-party analytics provider.
      </p>

      <h2>Access, correction and deletion</h2>
      <p>
        <Link href="/settings">Account settings</Link> lets you review signed-in
        devices, change your email or password, export and clear synced reading
        data, and delete your account. Deletion removes your account, sessions,
        follows, private-library access and synced data. It does not remove
        unrelated operational logs, previously aggregated traffic records or
        story copies retained for another reader or the public index.
      </p>
      <p>
        For an access, correction, deletion or other privacy request that the
        controls cannot handle, email{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a>. If a work in
        the index is yours, the <Link href="/permissions">author page</Link>
        explains how to restrict or remove it.
      </p>
    </main>
  )
}
