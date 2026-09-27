import { permanentRedirect } from "next/navigation"

// /account merged into /settings as its Account tab.
//
// The two pages were one page split by whether a setting happens to need an
// account: /settings held theme, reader font, search defaults, the content
// toggles, the mute list and the data controls, /account held identity, email,
// password, Google, devices and deletion — both rendering the same
// `.settings-group` sections in the same shell, and neither mentioning the
// other. The user menu linked "Account & sync" and no Settings at all.
//
// A permanent redirect rather than a client component that renders "Taking you
// to…" and calls router.replace: that shape answers 200 with a real page's worth
// of nothing, which this repo has already had to unpick twice (/takedowns and
// /permissions/manage). A 308 also tells search engines the address moved, which
// matters for the handful of places /account has been linked from.
//
// Kept indefinitely rather than deleted. It is in readers' history and
// bookmarks, it was the address the email prompt pointed at for months, and a
// redirect costs one file.
export default function AccountRedirect() {
  permanentRedirect("/settings?tab=account")
}
