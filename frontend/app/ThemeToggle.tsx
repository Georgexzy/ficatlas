"use client"

import { useEffect, useState } from "react"

type Mode = "system" | "light" | "dark"
const KEY = "ficatlas:theme"

// Site-wide light/dark, defaulting to whatever the device already asks for.
//
// "system" is the default rather than dark, because the OS setting is a
// preference the reader has already expressed — overriding it by default and
// making them find a toggle gets that backwards. It is also the setting that
// tracks sunset without anyone doing anything.
//
// The class is written to <html> in a blocking script (see layout), not here,
// because doing it in React means the page renders in the wrong theme for a
// frame first. On a dark-by-default site that flash is a white flash, which is
// exactly what someone reading at night does not want.
export function applyTheme(mode: Mode) {
  const root = document.documentElement
  const dark =
    mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)
  root.setAttribute("data-theme", dark ? "dark" : "light")
}

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [mode, setMode] = useState<Mode>("system")

  useEffect(() => {
    const saved = (localStorage.getItem(KEY) as Mode) || "system"
    setMode(saved)
    applyTheme(saved)
    // Follow the OS if that is what was chosen — otherwise a reader on "system"
    // stays on yesterday's theme until they reload.
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => {
      if ((localStorage.getItem(KEY) as Mode) === "system") applyTheme("system")
    }
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [])

  const choose = (m: Mode) => {
    setMode(m)
    localStorage.setItem(KEY, m)
    applyTheme(m)
  }

  const OPTIONS: { id: Mode; label: string; glyph: string }[] = [
    { id: "light",  label: "Light",  glyph: "☀" },
    { id: "system", label: "System", glyph: "◐" },
    { id: "dark",   label: "Dark",   glyph: "☾" },
  ]

  return (
    <div className={`theme-toggle ${compact ? "theme-toggle--compact" : ""}`}
      role="group" aria-label="Colour theme">
      {OPTIONS.map(o => (
        <button key={o.id} onClick={() => choose(o.id)}
          className={`theme-toggle__btn ${mode === o.id ? "is-on" : ""}`}
          aria-pressed={mode === o.id}
          title={o.id === "system" ? "Match your device setting" : `${o.label} theme`}>
          <span aria-hidden="true">{o.glyph}</span>
          {!compact && <span className="theme-toggle__label">{o.label}</span>}
        </button>
      ))}
    </div>
  )
}
