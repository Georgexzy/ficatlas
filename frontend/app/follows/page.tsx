import { permanentRedirect } from "next/navigation"

// /follows merged into /library as its Following tab.
//
// Five lists of the reader's own works sat across two destinations — Following
// here, and Bookmarks, Reading, Offline and their imports on /library — with
// nothing on either page saying the other existed. They all answer one question,
// "what have I got?", so they are one page; Following leads it because it is the
// only one of them that changes without the reader doing anything.
//
// A permanent redirect rather than a client page that renders "Taking you to…"
// and calls router.replace: that answers 200 with a real page's worth of
// nothing, which this repo has had to unpick twice already. The address is in
// readers' history and is what the header badge linked at for months, so it is
// kept rather than deleted.
export default function FollowsRedirect() {
  permanentRedirect("/library?tab=following")
}
