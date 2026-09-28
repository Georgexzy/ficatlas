import ForgotClient from "./../forgot/ForgotClient"

// THE ADDRESS IN THE RESET EMAIL.
//
// backend/api/password_reset.py builds `{SITE_URL}/reset?code=…`, and until now
// that route did not exist — every reset email pointed at a 404. It had never
// mattered because the site could not send mail at all; the moment outbound was
// switched on, the one link in the one email it sends was broken.
//
// Renders the same component as /forgot, with the code from the query string
// filled in, so somebody following the link lands on "choose a new password"
// rather than being asked for a username they have already proved.
export default async function ResetPage(
  { searchParams }: { searchParams: Promise<{ code?: string | string[] }> },
) {
  const { code } = await searchParams
  // A repeated ?code= yields an array; take the first rather than rendering
  // "a,b" into the field.
  const one = Array.isArray(code) ? code[0] : code
  return <ForgotClient initialCode={one ?? ""} />
}
