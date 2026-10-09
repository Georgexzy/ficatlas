"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

// Keep individual utterances bounded: browser voices often truncate a whole
// chapter when it is submitted as one enormous string. Split at sentence edges
// where possible and keep each piece below a conservative 2,800 characters.
function splitText(text: string): string[] {
  const out: string[] = []
  let rest = text.replace(/\s+/g, " ").trim()
  while (rest.length > 2800) {
    let cut = rest.lastIndexOf(". ", 2800)
    if (cut < 1200) cut = rest.lastIndexOf(" ", 2800)
    if (cut < 1) cut = 2800
    else cut += 1
    out.push(rest.slice(0, cut).trim())
    rest = rest.slice(cut).trim()
  }
  if (rest) out.push(rest)
  return out
}

export default function ReadAloud({ html, language }: { html: string; language: string }) {
  const chunks = useMemo(() => {
    if (typeof DOMParser === "undefined") return []
    const doc = new DOMParser().parseFromString(html, "text/html")
    return splitText(doc.body.textContent ?? "")
  }, [html])
  const [reading, setReading] = useState(false)
  const [paused, setPaused] = useState(false)
  const [error, setError] = useState("")
  const generation = useRef(0)
  const playChunk = useRef<(index: number, run: number) => void>(() => {})

  playChunk.current = (index, run) => {
    if (run !== generation.current || index >= chunks.length) {
      if (run === generation.current) { setReading(false); setPaused(false) }
      return
    }
    const utterance = new SpeechSynthesisUtterance(chunks[index])
    utterance.lang = language
    utterance.onend = () => playChunk.current(index + 1, run)
    utterance.onerror = event => {
      if (run !== generation.current || event.error === "canceled" || event.error === "interrupted") return
      generation.current++
      setReading(false); setPaused(false)
      setError("Your browser could not read this chapter aloud.")
    }
    window.speechSynthesis.speak(utterance)
  }

  const stop = useCallback(() => {
    generation.current++
    if (typeof window !== "undefined" && window.speechSynthesis) window.speechSynthesis.cancel()
    setReading(false); setPaused(false)
  }, [])

  useEffect(() => {
    generation.current++
    if (typeof window !== "undefined" && window.speechSynthesis) window.speechSynthesis.cancel()
    setReading(false); setPaused(false)
    return () => {
      generation.current++
      if (typeof window !== "undefined" && window.speechSynthesis) window.speechSynthesis.cancel()
    }
  }, [html])

  const toggle = () => {
    setError("")
    if (typeof window === "undefined" || !window.speechSynthesis || typeof SpeechSynthesisUtterance === "undefined") {
      setError("Text to speech is not available in this browser.")
      return
    }
    if (paused) {
      window.speechSynthesis.resume()
      setPaused(false)
      return
    }
    if (reading) {
      window.speechSynthesis.pause()
      setPaused(true)
      return
    }
    if (!chunks.length) {
      setError("There is no chapter text to read aloud.")
      return
    }
    window.speechSynthesis.cancel()
    const run = ++generation.current
    setReading(true); setPaused(false)
    playChunk.current(0, run)
  }

  return <>
    <button type="button" className={`reader-ctrl reader-ctrl--listen${reading ? " is-on" : ""}`}
      onClick={toggle} aria-pressed={reading && !paused}
      aria-label={paused ? "Resume narration" : reading ? "Pause narration" : "Listen to this chapter"}
      title={paused ? "Resume narration" : reading ? "Pause narration" : "Listen to this chapter"}>
      {paused ? "▶" : reading ? "Ⅱ" : "▶"}
    </button>
    {reading && <button type="button" className="reader-ctrl reader-ctrl--listen-stop"
      onClick={stop} aria-label="Stop narration" title="Stop narration">■</button>}
    {error && <span className="reader-listen-error" role="status">{error}</span>}
  </>
}
