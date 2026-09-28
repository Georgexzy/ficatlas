import ForgotClient from "./../forgot/ForgotClient"

// THE ADDRESS IN THE RESET EMAIL.
//
// password_reset.py builds `{SITE_URL}/reset#code=…`, and until recently this
// route did not exist at all — every reset email pointed at a 404. It had never
// mattered because the site could not send mail and SITE_URL was unset, so no
// link was ever generated.
//
// THE CODE ARRIVES IN THE FRAGMENT, so this component cannot read it and
// deliberately does not try. A fragment is never sent to a server: not in the
// request line, not in Referer, not to a CDN. That is the point of using one —
// nginx logs full query strings (verified in today's access log) and this site
// loads Google Fonts, so `?code=` would have written a live single-use token
// into the access log and handed it to a third party in a Referer header.
//
// ForgotClient reads location.hash on mount instead.
export default function ResetPage() {
  return <ForgotClient fromHash />
}
