"use client"

import { useState } from "react"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import BackLink from "../BackLink"

export default function FeedbackForm() {
  const [text, setText] = useState("")
  const [email, setEmail] = useState("")
  const [status, setStatus] = useState<"idle" | "sending" | "ok" | "error">("idle")
  const [error, setError] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    setStatus("sending")
    setError("")
    try {
      const res = await fetch("/api/feedback/form", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          text: text.trim(),
          email: email.trim(),
          page: window.location.href,
        }),
      })
      if (!res.ok) throw new Error("Submission failed")
      setStatus("ok")
      setText("")
      setEmail("")
    } catch {
      setStatus("error")
      setError("Could not send — you can also email help@ficatlas.com directly.")
    }
  }

  return (
    <>
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />

      <h1>Feedback & suggestions</h1>

      <p className="page-prose__muted">
        No account needed. This goes straight to the owner's inbox.
      </p>

      {status === "ok" && (
        <div className="feedback__success">
          <p>✓ Sent — thank you. Every message is read.</p>
          <p className="page-prose__muted">
            <Link href="/">Search again</Link> or <Link href="/about">read more about FicAtlas</Link>.
          </p>
        </div>
      )}

      {status !== "ok" && (
        <form onSubmit={handleSubmit} className="feedback__form">
          <label htmlFor="feedback-text" className="feedback__label">
            What would you like to say?
          </label>
          <textarea
            id="feedback-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What works? What's broken? What do you wish existed?
Whether it's a missing fandom, a search that gave nothing, a feature idea,
or just 'I like this' — it all helps."
            rows={8}
            className="feedback__textarea"
            required
            maxLength={4000}
          />
          <div className="feedback__charcount">
            {text.length}/4000
          </div>

          <label htmlFor="feedback-email" className="feedback__label">
            Email (optional — only if you're open to a follow-up question)
          </label>
          <input
            id="feedback-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="feedback__input"
            maxLength={254}
          />

          {error && <p className="feedback__error">{error}</p>}

          <button
            type="submit"
            disabled={status === "sending" || !text.trim()}
            className="card-btn card-btn--primary feedback__submit"
          >
            {status === "sending" ? "Sending…" : "Send feedback"}
          </button>
        </form>
      )}

      <hr className="feedback__divider" />

      <h2>Other ways to reach us</h2>
      <ul className="feedback__links">
        <li>
          <Link href="/about#contact">Contact & author requests</Link> — for takedowns, permissions,
          or anything about a specific work.
        </li>
        <li>
          <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> — email directly.
        </li>
        <li>
          <a href="https://github.com/Georgexzy/ficatlas/issues" target="_blank" rel="noopener noreferrer">
            GitHub issues
          </a> — for bugs, feature requests, or if you want to contribute.
        </li>
      </ul>

      <p className="page-prose__muted">
        FicAtlas is a non-commercial, open-source project. There is no support team —
        just one person building this in their spare time. Your patience is appreciated.
      </p>
    </>
  )
}