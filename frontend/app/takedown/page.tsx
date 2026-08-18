"use client"

import Link from "next/link"
import BackLink from "../BackLink"
import { useEffect, useState } from "react"
import SiteHeader from "../SiteHeader"

// The person filling this in is an author who has found their work somewhere
// they did not put it. They may be upset, they are not necessarily technical,
// and they should not have to read anything to use it. So: four fields, plain
// words, no legal vocabulary, and an answer that says what actually happened
// rather than "your ticket has been received".
export default function TakedownPage() {
  const [sent, setSent] = useState<null | { hidden: boolean; delisted?: boolean; message: string }>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [penName, setPenName] = useState("")
  const [hosted, setHosted] = useState<any[]>([])
  const [checked, setChecked] = useState(false)
  const [checking, setChecking] = useState(false)

  // Arriving from "Is this your work?" on a story page, the address is already
  // known — asking the author to fetch it themselves is asking them to go back,
  // copy a URL and return, at the one moment they are least inclined to.
  //
  // Read from window rather than useSearchParams(): that hook opts the whole
  // route into dynamic rendering unless it is wrapped in Suspense, and this page
  // must stay reachable even when things are going wrong.
  const [storyUrl, setStoryUrl] = useState("")
  useEffect(() => {
    const u = new URLSearchParams(window.location.search).get("url")
    if (u) setStoryUrl(u)
  }, [])
  const [delist, setDelist] = useState(false)

  async function runCheck() {
    if (penName.trim().length < 2) return
    setChecking(true)
    try {
      const r = await fetch(`/api/takedown/check?author=${encodeURIComponent(penName.trim())}`)
      const d = await r.json()
      setHosted(d.hosted || []); setChecked(true)
    } catch { setHosted([]); setChecked(true) }
    finally { setChecking(false) }
  }

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const r = await fetch("/api/takedown", { method: "POST", body: new FormData(e.currentTarget) })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || "Something went wrong. Please try again.")
      setSent(data)
    } catch (err: any) {
      setError(err.message || "Something went wrong. Please try again.")
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <div className="page-prose">
        <SiteHeader />
      <BackLink fallback="/about" fallbackLabel="About FicAtlas" />
        <h1>{sent.hidden || sent.delisted
          ? "The story has been taken down"
          : "Your request has been sent"}</h1>
        <p>{sent.message}</p>
        {!sent.hidden && !sent.delisted && (
          <p className="page-prose__muted">
            We could not match that address to a story whose text we host — it may
            already be listing-only, in which case there is no text to remove. We
            will still read your message.
          </p>
        )}
        {/* Does not promise an email, because none is sent.
            takedown.py stores the address and never mails it, and no SMTP is
            configured on this deployment at all — so "we will email you when
            someone has looked at it" was a promise the system could not keep,
            made to someone who has just found their writing somewhere they did
            not put it. The password-reset flow already settled this for the same
            situation: "check your inbox" when nothing was sent is how a flow
            becomes a support burden.
            What replaces it is better news anyway — the thing they wanted has
            already happened and they are not waiting on anyone. */}
        <p>
          You do not need to wait for a reply, and nothing further is required
          from you. A person reviews these, but the removal has already taken
          effect.
        </p>
        <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
      </div>
    )
  }

  return (
    <div className="page-prose">
      <SiteHeader />
      {/* Replaces a hard-coded "← About FicAtlas", which was a guess about
          where you came from — and wrong for anyone arriving from the footer,
          from search, or from a shared link. */}
      <BackLink fallback="/about" fallbackLabel="About FicAtlas" />

      <h1>Request a takedown</h1>
      <p>
        If you wrote a story whose text can be read on FicAtlas and you would
        rather it were not here, fill this in. The text comes down{" "}
        <strong>immediately</strong> — you do not have to wait for a reply, and
        you do not have to prove anything first.
      </p>
      <p className="page-prose__muted">
        By default the story stays listed as a title, author and link, so readers
        can still find your work where you publish it now. There is a box below
        to remove the listing as well.
      </p>

      {/* Says plainly what we do and do not do with this form. Two reasons it
          is worth the space: an author is entitled to know that nobody will
          demand they out themselves, and anyone considering abusing the form
          should know it does not delete anything. */}
      <div className="takedown-note">
        <p><strong>We will not ask you to prove it.</strong> Fandom runs on pen
        names, and asking someone to document their identity to reclaim their own
        writing gets it backwards. Most of the stories hosted here came from
        FictionAlley, an archive that closed — there is no account left to prove
        anything with even if we wanted it.</p>
        <p><strong>Nothing is deleted.</strong> A request hides the text
        immediately and permanently as far as readers are concerned, but it stays
        recoverable, so a mistaken or malicious request can be undone. That is
        also why the form cannot be used to wipe the library: hiding is
        reversible, and a burst of requests is reviewed by a person instead of
        acted on automatically.</p>
      </div>

      {/* Check first, ask second.
          A takedown form only helps someone who already knows their work is
          here. For text we host, expecting the author to discover that on their
          own is the weak part of the arrangement — so this looks it up for them
          before asking them to fill anything in. */}
      <div className="author-check">
        <p className="author-check__lead">
          <strong>Not sure if anything of yours is here?</strong> Type the name
          you write under. This only lists stories whose full text can be read on
          FicAtlas — not the millions we merely link to.
        </p>
        <div className="author-check__row">
          <input value={penName} onChange={e => setPenName(e.target.value)}
            placeholder="Your pen name" aria-label="Your pen name"
            onKeyDown={e => { if (e.key === "Enter") runCheck() }} />
          <button type="button" className="card-btn" onClick={runCheck} disabled={checking}>
            {checking ? "Checking…" : "Check"}
          </button>
        </div>
        {checked && (
          hosted.length === 0 ? (
            <p className="author-check__result">
              Nothing under that name is readable here. If you write under a
              different name, try that too.
            </p>
          ) : (
            <div className="author-check__result">
              <p><strong>{hosted.length}</strong> {hosted.length === 1 ? "story" : "stories"} under
                that name can be read here:</p>
              <ul>
                {hosted.map(h => (
                  <li key={h.id}>
                    {h.title} {h.withdrawn && <em>— already taken down</em>}
                  </li>
                ))}
              </ul>
              <p className="page-prose__muted">
                Fill the form below to have them removed. You can paste any one of
                the links, or just say &ldquo;all of them&rdquo; in the message.
              </p>
            </div>
          )
        )}
      </div>

      <form onSubmit={submit} className="takedown-form">
        <label>
          <span>Address of the story</span>
          <input name="story_url" required value={storyUrl}
            onChange={e => setStoryUrl(e.target.value)}
            placeholder="A FicAtlas link, or the story's page on AO3 or FFN" />
        </label>

        <label>
          <span>Your name</span>
          <input name="claimant" required placeholder="The name you write under is fine" />
        </label>

        <label>
          <span>Your email</span>
          <input name="email" type="email" required
            placeholder="In case we need to ask you something" />
        </label>

        <label>
          <span>You are</span>
          <select name="relationship" defaultValue="author">
            <option value="author">the author of this story</option>
            <option value="agent">acting for the author</option>
            <option value="other">someone else</option>
          </select>
        </label>

        {/* The page has always said "if you want the listing gone as well, say
            so below", and for a long time there was nothing behind that
            sentence — the message reached a human and no mechanism existed.
            A checkbox instead of a sentence in a free-text box, because a
            request typed in prose only works if somebody reads it. */}
        <label className="takedown-form__check">
          <input type="checkbox" name="delist" value="true"
            checked={delist} onChange={e => setDelist(e.target.checked)} />
          <span>
            <strong>Remove the listing too.</strong> By default the title,
            author and a link to where you publish stay, so readers can still
            find your work at its own home. Tick this and the entry disappears
            from search entirely.
          </span>
        </label>

        <label>
          <span>Anything you want to add <em>(optional)</em></span>
          <textarea name="detail" rows={4}
            placeholder="Anything you would like us to know." />
        </label>

        {error && <p className="takedown-form__error">{error}</p>}

        <button type="submit" className="card-btn card-btn--primary" disabled={busy}>
          {busy ? "Sending…" : "Take this story down"}
        </button>
      </form>

      {/* Offered after the form, never instead of it. Someone who arrived here
          wants their work down and should not have to read about alternatives
          first — but "all of it, permanently, including anything I write later"
          is a thing people mean and this form cannot express. */}
      <p className="takedown-alt">
        Want this to cover everything you write, not just one story? You can{" "}
        <Link href="/permissions">see everything held under your name</Link>{" "}
        and set a standing choice. That needs no proof either, unless you are
        giving permission rather than withdrawing it.
      </p>
    </div>
  )
}
