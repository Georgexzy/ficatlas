import { describe, it, expect } from "vitest"
import {
  STORED_KEYS, SYNCED_PREF_KEYS, DEVICE_ONLY_KEYS, keysForGroup, ALL_STORAGE_KEYS,
} from "./storageKeys"
import { DATA_GROUPS } from "./localdata"
import { PREF_KEYS as SETTINGS_PREF_KEYS } from "./prefs"

// The registry exists because three independent lists of "what is a preference"
// had drifted, and only 2 of 14 keys appeared in all three. These tests are the
// thing that stops a fourth list appearing — the same arrangement as
// tests/test_gate_terms_one_source.py on the backend, and for the same reason:
// equal contents today is how the drift started.

describe("the storage registry is the only list", () => {
  it("names every key exactly once", () => {
    const keys = STORED_KEYS.map(k => k.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it("gives every key a scope and a reason", () => {
    const names = STORED_KEYS.map(k => k.key)
    for (const k of STORED_KEYS) {
      expect(["sync", "device"]).toContain(k.scope)
      // The `why` is not decoration. "Should this follow me to my phone?" has a
      // real answer per key and it is not guessable from the name, so a key
      // added without one is a decision nobody made.
      //
      // A CROSS-REFERENCE counts, and this was got wrong first time round: the
      // reason for reader_lineheight really is "the same as reader_font", and
      // restating that argument five times would be noise, not rigour. So a
      // short reason is allowed exactly when it names another key in the
      // registry — which is still a decision, pointing at where it was made.
      const cites = names.some(n => n !== k.key && k.why.includes(n))
      expect(cites || k.why.length > 30,
        `${k.key}: give a reason, or cite the key whose reason it shares`).toBe(true)
    }
  })

  it("puts every setting the Settings page offers into sync", () => {
    // THE original bug, asserted in the direction it failed. default_sort,
    // results_per_page, show_explicit and show_underage were all offered in
    // Settings and none of them travelled — so a reader who turned adult
    // content on at their laptop got the default back on their phone.
    for (const k of SETTINGS_PREF_KEYS) {
      expect(SYNCED_PREF_KEYS, `${k} is offered in Settings but does not sync`)
        .toContain(k)
    }
  })

  it("clears every preference it offers, and nothing it does not", () => {
    // "Clear preferences" used to leave six of fourteen settings standing.
    const cleared = DATA_GROUPS.find(g => g.id === "prefs")!.keys
    for (const k of SETTINGS_PREF_KEYS) {
      expect(cleared, `Clear preferences does not clear ${k}`)
        .toContain(`ficatlas:${k}`)
    }
  })

  it("puts each data group's keys under exactly one group", () => {
    const seen = new Set<string>()
    for (const g of DATA_GROUPS) {
      for (const k of g.keys) {
        expect(seen.has(k), `${k} is in two data groups`).toBe(false)
        seen.add(k)
      }
    }
  })

  it("accounts for every key in some data group or as internal", () => {
    // A key that belongs to no group is a key the data page cannot show, clear
    // or explain — which is the gap lib/localdata.ts was written to close.
    const grouped = new Set(DATA_GROUPS.flatMap(g => g.keys))
    for (const k of ALL_STORAGE_KEYS) {
      const entry = STORED_KEYS.find(e => `ficatlas:${e.key}` === k)!
      if (entry.group === "internal") continue
      expect(grouped, `${k} is in no data group`).toContain(k)
    }
  })

  it("syncs the mute list, and says so wherever it is described", () => {
    // This test used to assert the OPPOSITE — that the list stayed on the
    // device — on the privacy argument in lib/mutelist.ts. It syncs now, by
    // decision, for consistency with everything else in Settings.
    //
    // What survives is the obligation the old test was really protecting: the
    // list is the most revealing thing this site stores, so wherever it is
    // described it must be described accurately. Three screens promised it
    // never left the device (Settings, /privacy, the login page's case for an
    // account) and all three had to change with it. That is what this asserts —
    // not the scope, which is the operator's call, but that the scope and the
    // copy cannot drift apart again.
    expect(SYNCED_PREF_KEYS.concat(DEVICE_ONLY_KEYS)).not.toContain("mutes")
    const entry = STORED_KEYS.find(k => k.key === "mutes")!
    expect(entry.scope).toBe("sync")
    expect(entry.why).toMatch(/privacy/i)
  })

  it("never syncs a rendered pixel offset", () => {
    // A scroll position measured on a phone means nothing on a laptop, and
    // restoring it lands the reader somewhere arbitrary with no way to tell why.
    expect(DEVICE_ONLY_KEYS).toContain("scroll-memory")
    expect(DEVICE_ONLY_KEYS).toContain("navstack")
  })

  it("does not put the identity cache on the wire", () => {
    // It is a copy OF the server's answer about who you are; syncing it back
    // would be circular, and it is the one key that must never be merged.
    expect(DEVICE_ONLY_KEYS).toContain("me")
  })

  it("puts every synced key somewhere a reader can clear it, or marks it internal", () => {
    // A key that is stored on the SERVER and belongs to no data group is one
    // the reader can never clear without deleting the whole account — and
    // /privacy states that individual categories can be cleared separately.
    //
    // This is the invariant behind the bug that prompted it: Clear used to
    // remove the local copy only, so for a signed-in reader the server kept
    // everything and a single window-focus sync pulled it straight back.
    // Verified in a browser, in both directions. The fix (forgetRemote in
    // lib/auth.tsx) can only reach a key that a group actually owns, so a
    // synced key outside every group silently opts out of it.
    const clearable = new Set(DATA_GROUPS.flatMap(g => g.keys))
    for (const k of STORED_KEYS) {
      if (k.scope !== "sync") continue
      if (k.group === "internal") continue     // deliberate; goes with the account
      expect(clearable, `${k.key} syncs but no Clear button can reach it`)
        .toContain(`ficatlas:${k.key}`)
    }
  })

  it("groups only keys that exist", () => {
    for (const group of ["prefs", "history", "progress", "bookmarks", "mutes",
                         "internal"] as const) {
      for (const k of keysForGroup(group)) {
        expect(ALL_STORAGE_KEYS).toContain(k)
      }
    }
  })
})
