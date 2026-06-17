"use client"
import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from "react"

export interface User { username: string; id: string }

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

// Keys we mirror between localStorage and the server.
const SYNC_KEYS = ["bookmarks", "progress", "recents"] as const
type SyncKey = typeof SYNC_KEYS[number]

// Pull each key from server and write to localStorage.
// Called on login / on app load if a session is already present.
async function pullFromServer() {
  try {
    const r = await fetch("/api/userdata", { credentials: "include" })
    if (!r.ok) return
    const data = await r.json()
    for (const k of SYNC_KEYS) {
      if (data[k] != null) {
        localStorage.setItem(`ficatlas:${k}`, JSON.stringify(data[k]))
      }
    }
    // Tell tabs/components to re-read
    window.dispatchEvent(new Event("ficatlas:storage-pulled"))
  } catch {}
}

// Push current localStorage state to the server for one key.
async function pushOne(key: SyncKey) {
  try {
    const raw = localStorage.getItem(`ficatlas:${key}`)
    if (raw == null) return
    await fetch(`/api/userdata/${key}`, {
      method: "PUT", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: raw,   // already JSON
    })
  } catch {}
}

let _syncTimers: Record<string, ReturnType<typeof setTimeout>> = {}
let _isLoggedIn = false
let _hooked = false

function hookLocalStorage() {
  if (_hooked) return
  if (typeof window === "undefined" || !window.localStorage) return
  _hooked = true
  const original = localStorage.setItem.bind(localStorage)
  localStorage.setItem = function(key: string, value: string) {
    original(key, value)
    if (!_isLoggedIn) return
    const m = key.match(/^ficatlas:(.+)$/)
    if (!m) return
    const k = m[1] as SyncKey
    if (!SYNC_KEYS.includes(k)) return
    if (_syncTimers[k]) clearTimeout(_syncTimers[k])
    _syncTimers[k] = setTimeout(() => { pushOne(k) }, 1500)
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  // Bootstrap: check session on mount
  useEffect(() => {
    hookLocalStorage()
    fetch("/api/auth/me", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const u = d?.user ?? null
        setUser(u)
        _isLoggedIn = !!u
        if (u) pullFromServer()
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const fd = new FormData()
    fd.append("username", username)
    fd.append("password", password)
    const r = await fetch("/api/auth/login", {
      method: "POST", body: fd, credentials: "include",
    })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Login failed")
    }
    const d = await r.json()
    setUser(d); _isLoggedIn = true
    await pullFromServer()
  }, [])

  const signup = useCallback(async (username: string, password: string) => {
    const fd = new FormData()
    fd.append("username", username)
    fd.append("password", password)
    const r = await fetch("/api/auth/signup", {
      method: "POST", body: fd, credentials: "include",
    })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || "Signup failed")
    }
    const d = await r.json()
    setUser(d); _isLoggedIn = true
    // For a new signup, server is empty; push local state up so a fresh
    // account on a phone inherits the user's existing localStorage data.
    for (const k of SYNC_KEYS) await pushOne(k)
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "include" })
    } catch {}
    setUser(null); _isLoggedIn = false
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}
