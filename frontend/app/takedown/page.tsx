"use client"

import Link from "next/link"
import { useState } from "react"

// The person filling this in is an author who has found their work somewhere
// they did not put it. They may be upset, they are not necessarily technical,
// and they should not have to read anything to use it. So: four fields, plain
// words, no legal vocabulary, and an answer that says what actually happened
// rather than "your ticket has been received".
export default function TakedownPage() {
  const [sent, setSent] = useState<null | { hidden: boolean; message: string }>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [penName, setPenName] = useState("")
  const [hosted, setHosted] = useState<any[]>([])
  const [checked, setChecked] = useState(false)
  const [checking, setChecking] = useState(false)

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
        <h1>{sent.hidden ? "The story has been taken down" : "Your request has been sent"}</h1>
        <p>{sent.message}</p>
        {!sent.hidden && (
          <p className="page-prose__muted">
            We could not match that address to a story whose text we host — it may
            already be listing-only, in which case there is no text to remove. We
            will still read your message.
          </p>
        )}
        <p>We will email you when someone has looked at it.</p>
        <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
      </div>
    )
  }

  return (
    <div className="page-prose">
      <p className="page-prose__back"><Link href="/about">← About FicAtlas</Link></p>

      <h1>Request a takedown</h1>
      <p>
        If you wrote a story whose text can be read on FicAtlas and you would
        rather it were not here, fill this in. The text comes down{" "}
        <strong>immediately</strong> — you do not have to wait for a reply, and
        you do not have to prove anything first.
      </p>
      <p className="page-prose__muted">
        The story stays listed as a title, author and link, so readers can still
        find your work where you publish it now. If you want the listing gone as
        well, say so below.
      </p>

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
          <input name="story_url" required placeholder="A FicAtlas link, or the story's page on AO3 or FFN" />
        </label>

        <label>
          <span>Your name</span>
          <input name="claimant" required placeholder="The name you write under is fine" />
        </label>

        <label>
          <span>Your email</span>
          <input name="email" type="email" required placeholder="So we can tell you what happened" />
        </label>

        <label>
          <span>You are</span>
          <select name="relationship" defaultValue="author">
            <option value="author">the author of this story</option>
            <option value="agent">acting for the author</option>
            <option value="other">someone else</option>
          </select>
        </label>

        <label>
          <span>Anything you want to add <em>(optional)</em></span>
          <textarea name="detail" rows={4}
            placeholder="For example: please remove the listing too." />
        </label>

        {error && <p className="takedown-form__error">{error}</p>}

        <button type="submit" className="card-btn card-btn--primary" disabled={busy}>
          {busy ? "Sending…" : "Take this story down"}
        </button>
      </form>
    </div>
  )
}
