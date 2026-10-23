"""Email the owner when a swarm arrives. Detection was never the problem.

The scrape of 2026-10-20 -> 10-23 ran for three days and was noticed only when
a human opened a panel. Everything that was supposed to see it DID see it:
`api.traffic.scrapers()` reported "swarm: 3 days, 42,501 visitors, 105,196
events, peak 239 a minute", the edge's own access log showed 5,310 story loads
carrying `Referer: https://ficatlas.com/?author=...`, and the traffic summary
kept the headline honest by counting the same rows as `script_*` and
`unverified_*` rather than as an audience. None of that reached anybody,
because every channel here is a page someone has to remember to open — and
this is the second incident recorded whose notice arrived through a person
wondering why a number looked wrong.

So this module adds the one thing that was missing: delivery. It reads the same
structural signal the panel already uses and mails the owner when it fires.

WHY E-MAIL AND NOT A ROW ON A DASHBOARD: the failure mode it addresses is the
absence of a human, so a channel that requires a human to look cannot be the
fix.

WHY AN HOUR, WHEN THE PANEL'S SWARM DETECTOR COUNTS PER MINUTE: the per-minute
rule (25 new unreferred visitors in one minute) missed most of that scrape,
which ran 14 a minute for 60 straight hours and peaked at 121. Measured over
the 30 days to 2026-10-23, an ordinary hour brings 1-8 visitors who arrived
from nowhere with no referrer — 329 hours in that band, and the busiest hour
that was not a known scrape was 14. So 25 in an HOUR is the same "not a real
crowd" constant at the grain where the slow pools actually live.

WHY THIS BLOCKS NOTHING, and it is worth being clear about: nothing here
refuses a request. The edge defences name a client, and a client can be
renamed; that scrape ran a real browser executing our own frontend from 1,351
addresses in an hour, so no request-level rule separates it from a reader
without also taxing readers. What the panel and this mail do is make the
arrival visible and quantified. The lever that reduces the cost is caching the
app routes it walks; the lever that would stop a patient copy of a public index
does not exist.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import text

log = logging.getLogger(__name__)

# New, unreferred visitors in one hour at which a mail is worth sending. The
# measured organic maximum is 14 an hour; 25 catches the two earlier unnoticed
# scrapes (88 an hour on 2026-09-30, 20-49 an hour across 2026-10-16) with a
# factor over every honest hour. Erring low is correct HERE in a way it is not
# for the panel's exclusions: a false alarm costs one mail, a missed swarm
# costs three days.
WATCH_PER_HOUR = 25

# Two swarms an hour apart are one incident. The 2026-10-20 scrape ran for 60
# hours; a mail every 15 minutes would have been 240 of them, and the second
# teaches nothing the first did not.
COOLDOWN_SEC = 6 * 3600

# The same structural definition as `_SWARM` in api/traffic.py, deliberately:
# "arrived with no external referrer" is the provenance signal that survived
# measurement, and activity-level rules were rejected there on the evidence
# (6,529 of that scrape's visitors had three or more events; the mean was 2.2
# either side). A referrer from our own domain is NOT evidence of a person —
# the scrape wrote `Referer: https://ficatlas.com/?author=...` into 5,310 story
# loads — which is what the NOT ILIKE below takes out.
_NEW_UNREFERRED = """
    WITH first_seen AS (
        SELECT visitor, date_trunc('hour', min(at)) AS h
          FROM visit_events_public
         WHERE at >= :since AND NOT bot
         GROUP BY visitor),
    referred AS (
        SELECT DISTINCT visitor
          FROM visit_events_public
         WHERE at >= :since AND ref_host IS NOT NULL
           AND kind <> 'out' AND ref_host NOT ILIKE '%ficatlas%')
"""


def check(db, minutes: int = 60, threshold: int = WATCH_PER_HOUR) -> dict | None:
    """The worst qualifying hour in the window, or None if there was none.

    The window bounds both halves of the signal — the new arrivals and the
    referrers that would excuse them — which is the same windowing the panel's
    detector uses. A visitor who arrived on Monday with a referrer and then
    searches from nowhere on Thursday is not caught, and that is the accepted
    shape of the rule rather than a gap to close later: it is the price of
    never counting a referred crowd as a swarm.
    """
    since = datetime.utcnow() - timedelta(minutes=minutes)
    row = db.execute(text(_NEW_UNREFERRED + """
        SELECT h, count(*)
          FROM first_seen
         WHERE visitor NOT IN (SELECT visitor FROM referred)
         GROUP BY h HAVING count(*) >= :threshold
         ORDER BY 2 DESC, 1 DESC LIMIT 1
    """), {"since": since, "threshold": threshold}).first()
    if row is None:
        return None
    hour, visitors = row[0], int(row[1])

    top: dict[str, list[tuple[str, int]]] = {}
    for kind, path, n in db.execute(text(_NEW_UNREFERRED + """
        SELECT v.kind, v.path, count(*)
          FROM visit_events_public v
         WHERE v.bot = false AND v.at >= :hour AND v.at < :until
           AND v.visitor IN (SELECT visitor FROM first_seen
                              WHERE h = :hour AND visitor NOT IN
                                    (SELECT visitor FROM referred))
         GROUP BY v.kind, v.path ORDER BY 3 DESC LIMIT 12
    """), {"since": since, "hour": hour,
           "until": hour + timedelta(hours=1)}).fetchall():
        top.setdefault(kind, []).append((path, int(n)))

    return {"hour": hour.isoformat(), "visitors": visitors, "top": top,
            "window_minutes": minutes}


def _owner_email(db) -> str:
    row = db.execute(text(
        "SELECT email FROM users WHERE role = 'owner' AND email <> '' "
        "ORDER BY created_at LIMIT 1")).first()
    return row[0] if row else ""


def run(db, now: float | None = None) -> str:
    """Check once. Returns "quiet", "sent", "throttled", "unconfigured" or
    "failed" — the last one deliberately distinct from "throttled", because
    "we already said so" and "the relay was down" are different days.
    """
    from mailer import send
    from api.settings import get_setting, put_setting

    now = time.time() if now is None else now
    hit = check(db)
    if hit is None:
        return "quiet"

    # Cooldown before delivery, so a 60-hour incident is one mail. Read before
    # the recipient check on purpose: "we know and we already said so" and
    # "nobody could be told" must not both look like silence in the log.
    last = get_setting(db, "scrape_watch_last_alert")
    if last and now - float(last) < COOLDOWN_SEC:
        return "throttled"

    to = _owner_email(db)
    if not to:
        return "unconfigured"

    from mailer import SITE_URL
    pages = "\n".join(f"    {p}  {n}" for p, n in hit["top"].get("page", [])[:6])
    searches = sum(n for _, n in hit["top"].get("search", [])[:5])
    body = f"""A swarm is on the site.

{hit["visitors"]} visitors arrived in the hour ending {hit["hour"]} with no
external referrer, past the {WATCH_PER_HOUR}/hour threshold at which this mail
is sent. An ordinary hour brings 1-8.

Top pages in that hour:
{pages or "    (none)"}

Searches from those visitors: {searches}

What this most likely is: the traffic panel already classifies arrivals of
this shape. Open {SITE_URL}/admin and read the scrapers section for the days,
the peak rate and the paths it walked.

What cannot be done about it: nothing at the edge stops a browser running our
own frontend from thousands of addresses without also taxing readers, and the
referrers it spoofs are ours.

What does help: caching the app routes it renders (/settings, /login,
/library, /permissions) so a crawl costs the edge rather than the origin, and
telling a reader from a replayer by Sec-Fetch-Site — now logged as `ss=` in
nginx but not yet measured on real traffic, so no edge rule may depend on it.
Full account in CLAUDE.md's gotcha on the 2026-10-20 scrape.
"""
    ok = send(to, f"FicAtlas: swarm detector fired ({hit['visitors']}/hour)", body)
    if ok:
        put_setting(db, "scrape_watch_last_alert", str(int(now)))
        db.commit()
        return "sent"
    # Not recorded as a cooldown on purpose: a relay that was down for an hour
    # should not buy itself six more hours of quiet.
    log.warning("scrape watch: %s/hour but the alert mail did not send",
                hit["visitors"])
    return "failed"
