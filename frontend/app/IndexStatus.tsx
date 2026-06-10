"use client"
import { useState, useEffect, useCallback } from "react"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface SiteSchedule {
  last_run: string | null
  next_run: string | null
  interval_hours: number
  last_job: {
    status: string
    stories_found: number
    stories_new: number
    stories_updated: number
    finished_at: string | null
  } | null
  active_job: {
    status: string
    stories_found: number
    started_at: string | null
  } | null
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function timeUntil(iso: string): string {
  const diff = new Date(iso).getTime() - Date.now()
  if (diff <= 0) return "soon"
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `in ${mins}m`
  const hrs = Math.floor(mins / 60)
  return `in ${hrs}h`
}

export default function IndexStatus() {
  const [schedule, setSchedule] = useState<Record<string, SiteSchedule> | null>(null)
  const [open, setOpen] = useState(false)

  const fetch_schedule = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/crawl/schedule`)
      if (res.ok) setSchedule(await res.json())
    } catch {}
  }, [])

  useEffect(() => {
    fetch_schedule()
    const interval = setInterval(fetch_schedule, 15000)
    return () => clearInterval(interval)
  }, [fetch_schedule])

  const anyActive = schedule && Object.values(schedule).some(s => s.active_job)
  const sites = ["ao3", "ffnet"]
  const LABELS: Record<string, string> = { ao3: "AO3", ffnet: "FF.net" }

  return (
    <div className="index-status">
      <button className={`index-status__btn ${anyActive ? "index-status__btn--active" : ""}`} onClick={() => setOpen(o => !o)}>
        {anyActive
          ? <><span className="crawl-dot crawl-dot--pulse" />Indexing…</>
          : <><span className="crawl-dot" />Index</>
        }
      </button>

      {open && schedule && (
        <div className="index-status__panel">
          <p className="index-status__heading">Index Status</p>
          {sites.map(site => {
            const s = schedule[site]
            if (!s) return null
            const active = s.active_job
            const last = s.last_job
            return (
              <div key={site} className="index-status__row">
                <div className="index-status__site-header">
                  <span className="index-status__site">{LABELS[site]}</span>
                  <span className="index-status__interval">every {s.interval_hours}h</span>
                </div>

                {active ? (
                  <div className="index-status__active">
                    <span className="crawl-dot crawl-dot--pulse" />
                    <span>{active.status === "pending" ? "Queued" : `Crawling — ${active.stories_found ?? 0} found so far`}</span>
                    <div className="index-status__bar">
                      <div className="index-status__bar-fill index-status__bar-fill--indeterminate" />
                    </div>
                  </div>
                ) : (
                  <div className="index-status__idle">
                    {last ? (
                      <span className="index-status__last">
                        Last indexed {timeAgo(last.finished_at!)} · +{last.stories_new} new · {last.stories_updated} updated
                      </span>
                    ) : (
                      <span className="index-status__last index-status__last--none">Never indexed</span>
                    )}
                    {s.next_run && (
                      <span className="index-status__next">Next run {timeUntil(s.next_run)}</span>
                    )}
                  </div>
                )}
              </div>
            )
          })}

          <p className="index-status__hint">
            Schedule is set by <code>CRAWL_INTERVAL_AO3_HOURS</code> / <code>CRAWL_INTERVAL_FFNET_HOURS</code> env vars.
          </p>
        </div>
      )}
    </div>
  )
}
