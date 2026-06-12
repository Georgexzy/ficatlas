"use client"
import { useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface ChapterFull {
  id: string; number: number; title?: string; summary?: string;
  content: string; word_count: number; posted_at?: string;
  start_note?: string; end_note?: string
}

interface StoryMin {
  id: string; title: string; author: string; chapter_count: number;
  chapters: { number: number; title?: string }[]
}

export default function ChapterPage() {
  const params = useParams()
  const router = useRouter()
  const storyId = params?.id as string
  const num = Number(params?.num)
  const [chapter, setChapter] = useState<ChapterFull | null>(null)
  const [story, setStory] = useState<StoryMin | null>(null)
  const [fontSize, setFontSize] = useState(17)

  useEffect(() => {
    const savedSize = localStorage.getItem("ficatlas:reader-fontsize")
    if (savedSize) setFontSize(Number(savedSize))
  }, [])

  useEffect(() => {
    localStorage.setItem("ficatlas:reader-fontsize", String(fontSize))
  }, [fontSize])

  useEffect(() => {
    if (!storyId || !num) return
    Promise.all([
      fetch(`${API_BASE}/api/stories/${storyId}`).then(r => r.json()),
      fetch(`${API_BASE}/api/stories/${storyId}/chapters/${num}`).then(r => r.json()),
    ]).then(([s, c]) => { setStory(s); setChapter(c) })
  }, [storyId, num])

  // Save last-read progress
  useEffect(() => {
    if (!story || !chapter) return
    const progress = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
    progress[story.id] = { chapter: chapter.number, at: new Date().toISOString(), title: story.title }
    localStorage.setItem("ficatlas:progress", JSON.stringify(progress))
  }, [story, chapter])

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.key === "ArrowLeft" && num > 1) router.push(`/story/${storyId}/chapter/${num - 1}`)
      if (e.key === "ArrowRight" && story && num < story.chapter_count) router.push(`/story/${storyId}/chapter/${num + 1}`)
      if (e.key === "+" || e.key === "=") setFontSize(s => Math.min(s + 1, 24))
      if (e.key === "-") setFontSize(s => Math.max(s - 1, 13))
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [num, story, storyId, router])

  if (!chapter || !story) return <div className="reader-shell"><p className="loading">Loading…</p></div>

  const hasPrev = num > 1
  const hasNext = num < story.chapter_count

  return (
    <div className="reader-shell">
      <div className="reader-topbar">
        <Link href={`/story/${storyId}`} className="back-link">← {story.title}</Link>
        <div className="reader-controls">
          <button className="reader-ctrl" onClick={() => setFontSize(s => Math.max(s - 1, 13))} aria-label="Smaller">A-</button>
          <button className="reader-ctrl" onClick={() => setFontSize(s => Math.min(s + 1, 24))} aria-label="Larger">A+</button>
        </div>
      </div>

      <article className="reader" style={{ fontSize: `${fontSize}px` }}>
        <header className="reader__header">
          <p className="reader__breadcrumb">Chapter {num} of {story.chapter_count}</p>
          <h1 className="reader__title">{chapter.title || `Chapter ${num}`}</h1>
          {chapter.summary && <p className="reader__summary">{chapter.summary}</p>}
        </header>

        {chapter.start_note && (
          <aside className="reader__note">
            <p className="reader__note-label">Author's Note</p>
            <div dangerouslySetInnerHTML={{ __html: chapter.start_note }} />
          </aside>
        )}

        <div className="reader__content" dangerouslySetInnerHTML={{ __html: chapter.content }} />

        {chapter.end_note && (
          <aside className="reader__note">
            <p className="reader__note-label">End Note</p>
            <div dangerouslySetInnerHTML={{ __html: chapter.end_note }} />
          </aside>
        )}
      </article>

      <nav className="reader-nav">
        <button className="reader-nav__btn" disabled={!hasPrev}
          onClick={() => router.push(`/story/${storyId}/chapter/${num - 1}`)}>← Previous</button>
        <Link href={`/story/${storyId}`} className="reader-nav__index">All chapters</Link>
        <button className="reader-nav__btn" disabled={!hasNext}
          onClick={() => router.push(`/story/${storyId}/chapter/${num + 1}`)}>Next →</button>
      </nav>

      <p className="reader-hint">← → to navigate · + − to resize</p>
    </div>
  )
}
