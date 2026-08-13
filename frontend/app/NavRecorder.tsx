"use client"

import { useEffect } from "react"
import { usePathname } from "next/navigation"
import { recordPath } from "@/lib/navStack"

// Writes down every page this tab visits, so "Back" can mean something.
//
// Mounted once in the root layout rather than per page: a page that forgot to
// record itself would be invisible to the stack, and the bug that produces —
// Back skipping a page you definitely visited — is worse and harder to spot
// than the one this fixes. Renders nothing.
export default function NavRecorder() {
  const pathname = usePathname()
  useEffect(() => {
    if (pathname) recordPath(pathname)
  }, [pathname])
  return null
}
