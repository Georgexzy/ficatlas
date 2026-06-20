"use client"
import { createContext, useContext, useEffect, useState, useCallback, useRef, ReactNode } from "react"

export interface User {
  username: string
  id: string
  created_at?: string | null
  last_login?: string | null
}

interface AuthContextType {
  user: User | null
  loading: boolean
  syncing: boolean
  lastSyncAt: number | null
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  syncNow: () => Promise<void>
  changePassword: (current: string, next: string) => Promise<void>
  deleteAccount: (password: string) => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

// Keys mirrored between localStorage and the server.
const SYNC_KEYS = ["bookmarks", "progress", "recents", "settings"] as const
type SyncKey = typeof SYNC_KEYS[number]

const LS = (k: string) => `ficatlas:${k}`

function readLocal(key: SyncKey): any {
  try {
    const raw = localStorage.getItem(LS(key))
    return raw == null ? null : JSON.parse(raw)
  } catch { return null }
}

function writeLocal(key: SyncKey, value: any) {
  try {
    // Write WITHOUT triggering our own setItem hook re-sync (use the raw original)
    _rawSetItem(LS(key), JSON.stringify(value))
  } catch {}
}

// Keep a reference to the unpatched setItem so server-driven writes don't loop.
let _rawSetItem: (k: string, v: string) => void =
  typeof window !== "undefined" && window.localStorage
    ? localStorage.setItem.bind(localStorage)
    : (() => {}) as any

let _hooked = false
function hookLocalStorage(onLocalChange: (key: SyncKey) => void) {
  if (_hooked) return
  if (typeof window === "undefined" || !window.localStorage) return
  _hooked = true
  _rawSetItem = localStorage.setItem.bind(localStorage)
  localStorage.setItem = function (key: string, value: string) {
    _rawSetItem(key, value)
    const m = key.match(/^ficatlas:(.+)$/)
    if (!m) return
    const k = m[1] as SyncKey
    if (SYNC_KEYS.includes(k)) onLocalChange(k)
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [lastSyncAt, setLastSyncAt] = useState<number | null>(null)

  const loggedInRef = useRef(false)
  const dirtyRef = useRef<Set<SyncKey>>(new Set())
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inFlightRef = useRef(false)
  // Hash of the last successfully-synced snapshot. Guards against the merge loop:
  // we skip the network call when local content matches what we already synced.
  const lastSyncedHashRef = useRef<string>("")

  // Build a snapshot of all local sync keys that have data.
  const snapshot = useCallback((): Record<string, any> => {
    const snap: Record<string, any> = {}
    for (const k of SYNC_KEYS) {
      const v = readLocal(k)
      if (v != null) snap[k] = v
    }
    return snap
  }, [])

  // Core sync: send a snapshot to /merge, adopt the merged result locally.
  // Robust against two-device divergence — the server merges, nothing is lost.
  //
  // CRITICAL: a content hash guards against an infinite sync loop. Adopting the
  // merged response writes to localStorage, and several handlers (focus/online/
  // interval) also trigger syncs; without comparing content we'd merge → write →
  // merge → write forever, hammering the backend many times per second. We only
  // hit the network when the local snapshot differs from what we last synced.
  const doMerge = useCallback(async (keysHint?: Set<SyncKey>, force = false) => {
    if (!loggedInRef.current) return
    if (inFlightRef.current) {
      if (keysHint) keysHint.forEach(k => dirtyRef.current.add(k))
      return
    }

    const body = snapshot()
    const bodyHash = JSON.stringify(body)
    // Nothing changed since the last successful sync → skip entirely. This is the
    // loop-breaker. `force` (login/signup/manual) bypasses it for a guaranteed sync.
    if (!force && bodyHash === lastSyncedHashRef.current) {
      dirtyRef.current.clear()
      return
    }

    inFlightRef.current = true
    setSyncing(true)
    try {
      const r = await fetch("/api/userdata/merge", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: bodyHash,
      })
      if (r.ok) {
        const merged = await r.json()
        // Adopt merged values locally (without re-triggering the setItem hook)
        for (const k of SYNC_KEYS) {
          if (merged[k] != null) writeLocal(k, merged[k])
        }
        // Record the hash of what's now local so identical re-syncs are skipped.
        lastSyncedHashRef.current = JSON.stringify(snapshot())
        setLastSyncAt(Date.now())
        dirtyRef.current.clear()
        window.dispatchEvent(new Event("ficatlas:storage-pulled"))
      } else {
        if (keysHint) keysHint.forEach(k => dirtyRef.current.add(k))
      }
    } catch {
      if (keysHint) keysHint.forEach(k => dirtyRef.current.add(k))
    } finally {
      inFlightRef.current = false
      setSyncing(false)
      // Only re-run if there are genuinely new dirty keys AND the content now
      // differs from what we just synced (prevents the write-back from looping).
      if (dirtyRef.current.size > 0 && JSON.stringify(snapshot()) !== lastSyncedHashRef.current) {
        const pending = new Set(dirtyRef.current)
        dirtyRef.current.clear()
        setTimeout(() => doMerge(pending), 800)
      } else {
        dirtyRef.current.clear()
      }
    }
  }, [snapshot])

  const scheduleSync = useCallback((key: SyncKey) => {
    if (!loggedInRef.current) return
    dirtyRef.current.add(key)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const pending = new Set(dirtyRef.current)
      dirtyRef.current.clear()
      doMerge(pending)
    }, 1200)
  }, [doMerge])

  // Bootstrap on mount.
  useEffect(() => {
    hookLocalStorage(scheduleSync)
    fetch("/api/auth/me", { credentials: "include" })
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        const u = d?.user ?? null
        setUser(u)
        loggedInRef.current = !!u
        if (u) doMerge(undefined, true)   // initial merge reconciles this device with the server
      })
      .catch(() => {})
      .finally(() => setLoading(false))

    // Re-sync when the tab regains focus or comes back online (robust catch-up).
    const onFocus = () => { if (loggedInRef.current) doMerge() }
    const onOnline = () => { if (loggedInRef.current) doMerge() }
    window.addEventListener("focus", onFocus)
    window.addEventListener("online", onOnline)
    // Periodic background reconcile so a long-open tab adopts edits from other devices.
    const interval = setInterval(() => { if (loggedInRef.current) doMerge() }, 60_000)
    return () => {
      window.removeEventListener("focus", onFocus)
      window.removeEventListener("online", onOnline)
      clearInterval(interval)
    }
  }, [scheduleSync, doMerge])

  // Flush pending sync before the page unloads (best-effort).
  useEffect(() => {
    const onBeforeUnload = () => {
      if (!loggedInRef.current || dirtyRef.current.size === 0) return
      try {
        const body = JSON.stringify(snapshot())
        navigator.sendBeacon?.("/api/userdata/merge", new Blob([body], { type: "application/json" }))
      } catch {}
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [snapshot])

  const login = useCallback(async (username: string, password: string) => {
    const fd = new FormData()
    fd.append("username", username); fd.append("password", password)
    const r = await fetch("/api/auth/login", { method: "POST", body: fd, credentials: "include" })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Login failed")
    }
    const d = await r.json()
    setUser(d); loggedInRef.current = true
    await doMerge(undefined, true)   // merge this device's local data with the account's server data
  }, [doMerge])

  const signup = useCallback(async (username: string, password: string) => {
    const fd = new FormData()
    fd.append("username", username); fd.append("password", password)
    const r = await fetch("/api/auth/signup", { method: "POST", body: fd, credentials: "include" })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Signup failed")
    }
    const d = await r.json()
    setUser(d); loggedInRef.current = true
    await doMerge(undefined, true)   // push existing local data up to the fresh account
  }, [doMerge])

  const logout = useCallback(async () => {
    // Flush any pending edits before signing out so nothing is lost.
    if (dirtyRef.current.size > 0) { try { await doMerge(undefined, true) } catch {} }
    try { await fetch("/api/auth/logout", { method: "POST", credentials: "include" }) } catch {}
    setUser(null); loggedInRef.current = false
  }, [doMerge])

  const syncNow = useCallback(async () => { await doMerge(undefined, true) }, [doMerge])

  const changePassword = useCallback(async (current: string, next: string) => {
    const fd = new FormData()
    fd.append("current_password", current); fd.append("new_password", next)
    const r = await fetch("/api/auth/change-password", { method: "POST", body: fd, credentials: "include" })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Couldn't change password")
    }
  }, [])

  const deleteAccount = useCallback(async (password: string) => {
    const fd = new FormData()
    fd.append("password", password)
    const r = await fetch("/api/auth/delete-account", { method: "POST", body: fd, credentials: "include" })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Couldn't delete account")
    }
    setUser(null); loggedInRef.current = false
  }, [])

  return (
    <AuthContext.Provider value={{
      user, loading, syncing, lastSyncAt,
      login, signup, logout, syncNow, changePassword, deleteAccount,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}
