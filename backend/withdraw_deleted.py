"""
Withdraw hosted text when the author has deleted the work at its source.
========================================================================

This is the one takedown decision that can be made without a human, and it is
worth making automatically because it is the commonest one by far.

The reasoning: a takedown form asks "are you really the author?", which a form
cannot answer — anyone can type a name. But if a work has been *deleted from
AO3 or FanFiction.net*, only the account that posted it could have done that.
The author has already withdrawn it, from the place they chose to publish it. Us
continuing to serve a full copy at that point is the clearest possible case of
hosting something against the author's wishes, and nobody has to ask.

So this walks hosted stories, re-checks the original URL, and withdraws the text
of anything that is gone. Metadata stays, as with any takedown — the entry
becomes a record that the work existed, not a copy of it.

Care taken about false positives, because withdrawing wrongly is a real cost:

  * 404 and 410 only. A 5xx, a timeout, a Cloudflare interstitial or a rate
    limit means "we could not check", not "it is gone".
  * AO3 answers 302 -> /users/login for adult and restricted works. That is a
    live work behind a gate, not a deleted one.
  * Confirmed twice, on separate passes at least CONFIRM_GAP_HOURS apart. A
    single 404 during an AO3 deploy would otherwise withdraw thousands.
  * Paced by the shared AO3 budget, like every other AO3 path.

Reversible: withdrawal is a flag, and clearing it restores the text. If a work
comes back, a later pass clears the strike.
"""

import logging
import os
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

CONFIRM_GAP_HOURS = float(os.getenv("WITHDRAW_CONFIRM_GAP_HOURS", "24"))
BATCH = int(os.getenv("WITHDRAW_BATCH", "40"))

# Archives that no longer exist. Their URLs cannot answer, so checking them is
# not evidence of anything and re-checking them daily is pure waste — 29,949 of
# the 29,978 hosted works here are FictionAlley, whose domain stopped resolving
# after it closed.
#
# This is also why "unreachable" must never mean "deleted": had it, the very
# first pass would have withdrawn essentially every hosted story on the site.
#
# For these works the source-deletion signal is unavailable by definition, and
# the takedown form (api/takedown.py) is the only route. Worth knowing that
# FictionAlley's catalogue moved to AO3 via Open Doors in 2018, so those authors do
# have their works somewhere they control — just not at the URL we hold.
DEAD_ARCHIVES = {"fictionalley"}

HEADERS = {
    # A bot's User-Agent is where a site operator looks to reach whoever is
# making the requests, so the contact in it has to be one that works. This
# said admin@ficatlas.app, an address on a domain nobody owns — worse than
# no contact at all, because it claims to be reachable. The repository is a
# real channel and needs no domain.
    "User-Agent": "FicAtlas/0.1 (fanfiction search index; +https://github.com/Georgexzy/ficatlas)",
    "Accept": "text/html,application/xhtml+xml",
}

# Hosted works only — there is nothing to withdraw for a metadata-only row — and
# never anything already withdrawn. Oldest check first so the sweep rotates.
CANDIDATES = sql_text("""
    SELECT s.id, s.url, s.site,
           COALESCE(c.strikes, 0) AS strikes, c.last_seen_gone
    FROM stories s
    LEFT JOIN source_gone c ON c.story_id = s.id
    WHERE s.is_hosted
      AND s.text_withdrawn_at IS NULL
      AND s.url IS NOT NULL
      AND s.site <> ALL(:dead)
      AND (c.last_checked IS NULL OR c.last_checked < now() - (:gap || ' hours')::interval)
    ORDER BY c.last_checked NULLS FIRST
    LIMIT :lim
""")


def check_source(url: str) -> str:
    """Return 'gone', 'alive' or 'unknown' for one source URL.

    'unknown' is the safe default and covers every case where we did not get a
    clear answer — the caller must not treat it as evidence of anything.
    """
    try:
        r = httpx.get(url, headers=HEADERS, timeout=45, follow_redirects=False)
    except httpx.RequestError:
        return "unknown"

    if r.status_code in (404, 410):
        return "gone"

    # AO3 sends adult and restricted works to a gate rather than a 404. Those are
    # live works, and treating the redirect as deletion would withdraw exactly
    # the mature fic most likely to be gated.
    #
    # The two gates are NOT the same thing and are no longer collapsed together:
    #
    #   view_adult   an age confirmation. Anyone can click through, logged in or
    #                not, so the work is as public as any other. Nothing to record.
    #   users/login  registered users only. The author has deliberately taken the
    #                work out of public view — roughly 966,000 of AO3's ~11.7M
    #                works are locked this way, largely in response to scraping
    #                and AI fears.
    #
    # Both stay in the index, because both still exist. But the second is an
    # author decision about visibility, and this was the only place in the system
    # that could see it and threw it away. Distinguishing them costs one branch.
    if r.status_code in (301, 302, 303, 307, 308):
        target = r.headers.get("location", "")
        if "users/login" in target or "restricted" in target:
            return "restricted"
        if "view_adult" in target:
            return "alive"
        return "unknown"

    if 200 <= r.status_code < 300:
        # FFN serves 200 with an error body for removed stories rather than 404.
        body = r.text[:4000].lower()
        for marker in ("story not found", "story is unavailable",
                       "the page you have requested does not exist"):
            if marker in body:
                return "gone"
        return "alive"

    return "unknown"


def run_pass(db, limit: int = BATCH, dry_run: bool = False) -> dict:
    """One sweep. Returns a summary dict."""
    import ao3_budget

    rows = db.execute(CANDIDATES, {"gap": CONFIRM_GAP_HOURS, "lim": limit,
                                   "dead": list(DEAD_ARCHIVES)}).fetchall()
    seen = withdrawn = cleared = unknown = restricted = 0

    for story_id, url, site, strikes, last_gone in rows:
        if "archiveofourown.org" in (url or ""):
            ao3_budget.BUDGET.wait()
        verdict = check_source(url)
        seen += 1

        if verdict == "unknown":
            unknown += 1
            db.execute(sql_text("""
                INSERT INTO source_gone (story_id, last_checked)
                VALUES (:id, now())
                ON CONFLICT (story_id) DO UPDATE SET last_checked = now()
            """), {"id": str(story_id)})
            continue

        if verdict == "restricted":
            restricted += 1
            # Alive for withdrawal purposes — strikes reset exactly as for any
            # other live work — but the visibility decision is recorded.
            db.execute(sql_text("""
                UPDATE stories SET source_restricted_at = COALESCE(source_restricted_at, now())
                WHERE id = CAST(:id AS uuid)
            """), {"id": str(story_id)})
            db.execute(sql_text("""
                INSERT INTO source_gone (story_id, strikes, last_checked)
                VALUES (:id, 0, now())
                ON CONFLICT (story_id) DO UPDATE
                  SET strikes = 0, last_checked = now(), last_seen_gone = NULL
            """), {"id": str(story_id)})
            continue

        if verdict == "alive":
            if strikes:
                cleared += 1
            # An author who unlocks a work has changed their mind back, and the
            # flag has to clear or it becomes a one-way door.
            db.execute(sql_text("""
                UPDATE stories SET source_restricted_at = NULL
                WHERE id = CAST(:id AS uuid) AND source_restricted_at IS NOT NULL
            """), {"id": str(story_id)})
            db.execute(sql_text("""
                INSERT INTO source_gone (story_id, strikes, last_checked)
                VALUES (:id, 0, now())
                ON CONFLICT (story_id) DO UPDATE
                  SET strikes = 0, last_checked = now(), last_seen_gone = NULL
            """), {"id": str(story_id)})
            continue

        # verdict == "gone"
        new_strikes = (strikes or 0) + 1
        db.execute(sql_text("""
            INSERT INTO source_gone (story_id, strikes, last_checked, last_seen_gone)
            VALUES (:id, :n, now(), now())
            ON CONFLICT (story_id) DO UPDATE
              SET strikes = :n, last_checked = now(), last_seen_gone = now()
        """), {"id": str(story_id), "n": new_strikes})

        # Two independent confirmations, hours apart, before anything is hidden.
        if new_strikes >= 2 and not dry_run:
            db.execute(sql_text("""
                UPDATE stories
                   SET text_withdrawn_at = now(),
                       text_withdrawn_reason = 'source deleted'
                 WHERE id = :id AND text_withdrawn_at IS NULL
            """), {"id": str(story_id)})
            withdrawn += 1
            log.info(f"withdrew hosted text: source deleted — {url}")

    db.commit()
    return {"checked": seen, "withdrawn": withdrawn,
            "cleared": cleared, "unknown": unknown, "restricted": restricted}
