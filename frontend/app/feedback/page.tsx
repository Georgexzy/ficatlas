import { Metadata } from "next"
import FeedbackForm from "./FeedbackForm"

export const metadata: Metadata = {
  title: "Feedback & suggestions",
  description:
    "Tell us what works, what doesn't, and what you wish FicAtlas could do. No account required.",
  alternates: { canonical: "/feedback" },
}

export default function Feedback() {
  return (
    <main id="main" className="page-prose">
      <FeedbackForm />
    </main>
  )
}