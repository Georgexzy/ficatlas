import ForgotClient from "./ForgotClient"

// The form itself lives in ForgotClient, shared with /reset. See the note at
// the top of that file for why the two addresses are one component.
export default function ForgotPage() {
  return <ForgotClient />
}
