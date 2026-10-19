"use client"

import { useEffect, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"
import Link from "next/link"

/**
 * Cloudflare Analytics — requests, bandwidth, threats, and unique visitors.
 *
 * The data comes from the worker's daily cf_analytics_loop, stored in
 * app_settings as cf_analytics_data and cf_analytics_fetched_at.
 * This is the second layer — the primary defence is the edge rules and
 * bot blocking at Cloudflare itself; this is the visibility layer.
 */
interface SeriesPoint {
  date: string
  requests: number
  bandwidth: number
  threats: number
  unique_visitors: number
}

interface CloudflareResponse {
  available: boolean
  fetched_at: string | null
  series: SeriesPoint[]
  message?: string
}

export default function CloudflarePanel() {
  const [data, setData] = useState<CloudflareResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    fetchWithTimeout("/api/admin/cloudflare-analytics", {}, USER_TIMEOUT_MS)
      .then(async r => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        const d = await r.json()
        if (mounted) setData(d)
      })
      .catch(e => {
        if (mounted) setError(e.message)
      })
      .finally(() => {
        if (mounted) setLoading(false)
      })
    return () => { mounted = false }
  }, [])

  if (loading) return <p className="cc-dim cc-loading">Loading Cloudflare analytics…</p>

  if (error) return <p className="cc-msg cc-msg--bad">Failed to load: {error}</p>

  if (!data || !data.available) {
    return (
      <div className="cc-cloudflare">
        <p className="cc-msg cc-msg--warn">
          {data?.message || "No analytics data available yet."}
        </p>
        <p className="cc-msg cc-msg--dim">
          The worker's cf_analytics_loop runs daily and stores a 30-day snapshot.
          Ensure <code>CF_ANALYTICS=true</code> is set in the worker environment
          and the worker has run at least once.
        </p>
        <p className="cc-msg cc-msg--dim">
          <Link href="/admin?tab=tools" className="cc-link">Check worker logs in Tools → DbActivity</Link>
        </p>
      </div>
    )
  }

  const series = data.series
  if (!series.length) {
    return <p className="cc-msg cc-msg--dim">No analytics data points yet.</p>
  }

  // Calculate totals
  const totalRequests = series.reduce((sum, p) => sum + p.requests, 0)
  const totalBandwidth = series.reduce((sum, p) => sum + p.bandwidth, 0)
  const totalThreats = series.reduce((sum, p) => sum + p.threats, 0)
  const avgVisitors = Math.round(series.reduce((sum, p) => sum + p.unique_visitors, 0) / series.length)

  // Last 7 days for the sparkline
  const recent = series.slice(-7)

  return (
    <div className="cc-cloudflare">
      <div className="cc-cloudflare__summary">
        <Stat label="Total Requests (30d)" value={formatNumber(totalRequests)} />
        <Stat label="Total Bandwidth (30d)" value={formatBytes(totalBandwidth)} />
        <Stat label="Threats Blocked (30d)" value={formatNumber(totalThreats)} tone="bad" />
        <Stat label="Avg Daily Visitors" value={formatNumber(avgVisitors)} />
      </div>

      <div className="cc-cloudflare__series">
        <h3>Last 30 Days</h3>
        <table className="cc-cloudflare__table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Requests</th>
              <th>Bandwidth</th>
              <th>Threats</th>
              <th>Unique Visitors</th>
            </tr>
          </thead>
          <tbody>
            {series.map(p => (
              <tr key={p.date}>
                <td>{p.date}</td>
                <td>{formatNumber(p.requests)}</td>
                <td>{formatBytes(p.bandwidth)}</td>
                <td className={p.threats > 0 ? "cc-cloudflare__threat" : ""}>{formatNumber(p.threats)}</td>
                <td>{formatNumber(p.unique_visitors)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="cc-cloudflare__sparkline" aria-label="Requests over last 7 days">
        <h4>Requests (last 7 days)</h4>
        <div className="cc-cloudflare__bars" role="img" aria-label={recent.map(p => `${p.date}: ${p.requests}`).join(", ")}>
          {recent.map(p => (
            <span
              key={p.date}
              className="cc-cloudflare__bar"
              style={{ height: `${Math.max(4, (p.requests / Math.max(...recent.map(r => r.requests))) * 100)}%` }}
              title={`${p.date}: ${formatNumber(p.requests)} requests`}
            />
          ))}
        </div>
        <div className="cc-cloudflare__bar-labels">
          {recent.map(p => <span key={p.date}>{p.date.slice(5)}</span>)}
        </div>
      </div>

      <p className="cc-cloudflare__note">
        Fetched at: {data.fetched_at ? new Date(data.fetched_at).toLocaleString() : "unknown"} ·
        <Link href="https://dash.cloudflare.com" target="_blank" rel="noopener noreferrer" className="cc-link">
          View full dashboard at Cloudflare
        </Link>
      </p>
    </div>
  )
}

interface StatProps {
  label: string
  value: string
  tone?: "ok" | "mid" | "bad" | "none"
  onClick?: () => void
}

function Stat({ label, value, tone = "none", onClick }: StatProps) {
  return (
    <div className={`cc-stat cc-stat--${tone}`} onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined}>
      <span className="cc-stat__label">{label}</span>
      <span className="cc-stat__value">{value}</span>
    </div>
  )
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return n.toLocaleString()
}

function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.round(n / 1024)} kB`
}