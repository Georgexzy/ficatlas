"""
Backfill FF.net genres, characters and engagement counts from the Wayback Machine.
================================================================================

The HuggingFace FF.net metadata dump this index was built from carries only
source_file, category, rating, chapters, words, story_url, summary and language.
No genres, no characters, no engagement counts — so 6.6M FF.net works have no
content tags at all, and the whole index has almost no popularity signal
(529 works out of 19.8M have kudos).

FF.net itself returns 403 to any server-side request, but archive.org's copies
are fetchable, and an archived story page carries the full metadata line:

    Rated: Fiction M - English - Adventure/Drama - Link, Zelda, Jon S., Tyrion L.
     - Chapters: 4 - Words: 4,433 - Reviews: 5 - Favs: 8 - Follows: 14

which yields genres, characters AND favs/follows/reviews.

This is one HTTP request per story, so it will never cover all 6.6M. It is a
backfill: run it against the stories that matter most (longest first by default,
since those are what people actually read), let it work through them over time,
and re-run whenever. Every row it touches is one that previously had no tags.

Usage
-----
    docker compose exec backend python ffnet_enrich.py --limit 200 --dry-run
    docker compose exec backend python ffnet_enrich.py --limit 5000
    docker compose exec backend python ffnet_enrich.py            # until exhausted
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session
from models.story import StatusEnum, Story

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

CDX = "http://web.archive.org/cdx/search/cdx"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# FF.net's fixed genre vocabulary. Used to tell the genre segment apart from the
# character segment, since neither is labelled and both are optional.
GENRES = {
    "Adventure", "Angst", "Crime", "Drama", "Family", "Fantasy", "Friendship",
    "General", "Horror", "Humor", "Hurt/Comfort", "Mystery", "Parody", "Poetry",
    "Romance", "Sci-Fi", "Spiritual", "Supernatural", "Suspense", "Tragedy",
    "Western",
}

LANGUAGES = {
    "English", "Spanish", "French", "German", "Portuguese", "Italian", "Dutch",
    "Polish", "Russian", "Chinese", "Japanese", "Korean", "Indonesian", "Filipino",
    "Finnish", "Swedish", "Norwegian", "Danish", "Hungarian", "Czech", "Romanian",
    "Turkish", "Greek", "Hebrew", "Arabic", "Ukrainian", "Vietnamese", "Thai",
    "Catalan", "Esperanto", "Latin", "Bulgarian", "Croatian", "Serbian",
}

_LABELLED = re.compile(
    r"^(Chapters|Words|Reviews|Favs|Follows|Published|Updated|Status|Rated|id)\s*:", re.I)


def _ffn_date(value: str):
    """FF.net writes dates two ways, both seen in the wild:

        "Updated: 11/26/2012"   M/D/YYYY
        "Published: 09-19-10"   MM-DD-YY

    Returned as UTC midnight. These were previously parsed and thrown away —
    the docstring below claimed "counts and dates" but only counts were kept,
    which is why 100% of 6,572,195 FF.net rows had no dates at all while the
    line right there on the page carried them.
    """
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _classify_genres(part: str) -> list[str]:
    """Split an unlabelled segment into FF.net genres, or [] if it is not genres.

    Must test the WHOLE string before splitting on "/", because "Hurt/Comfort"
    is itself a single genre. Splitting first turned it into ["Hurt","Comfort"],
    matched neither, and fell through to the character branch — which then also
    blocked the real character list, since that branch only fires when no
    characters have been recorded yet.
    """
    part = part.strip()
    if part in GENRES:
        return [part]
    pieces = [p.strip() for p in part.split("/") if p.strip()]
    if pieces and all(p in GENRES for p in pieces):
        return pieces
    # "Hurt/Comfort/Romance" — recombine adjacent pieces that form a real genre.
    out: list[str] = []
    i = 0
    while i < len(pieces):
        if i + 1 < len(pieces) and f"{pieces[i]}/{pieces[i+1]}" in GENRES:
            out.append(f"{pieces[i]}/{pieces[i+1]}")
            i += 2
        elif pieces[i] in GENRES:
            out.append(pieces[i])
            i += 1
        else:
            return []
    return out


# Off by default. This is a BULK background sweep, and FicHub is one volunteer's
# server with no published rate limit — sending it the ~31% of FF.net works that
# Wayback missed trips its per-IP throttle and keeps the whole IP blocked,
# including the on-demand user imports that also go through FicHub. Bulk sweeps
# belong on the Internet Archive; leave FicHub for the low-volume, user-initiated
# import path. Turn it back on with FFNET_FICHUB_FALLBACK=true if desired.
_FICHUB_FALLBACK = os.getenv("FFNET_FICHUB_FALLBACK", "false").lower() in ("1", "true", "yes")
_fichub_client = None


def _fichub_fallback(site_id: str) -> dict | None:
    """Ask FicHub for a work archive.org never captured, in ffnet_enrich's shape."""
    global _fichub_client
    try:
        import httpx
        from fichub_meta import fetch_meta as fh_meta, HEADERS as FH_HEADERS
        if _fichub_client is None:
            _fichub_client = httpx.Client(headers=FH_HEADERS, follow_redirects=True)
        d = fh_meta(_fichub_client, f"https://www.fanfiction.net/s/{site_id}/1/")
    except Exception:
        return None
    if not d:
        return None
    # Translate to the keys _write_batch already understands.
    return {
        "genres": d.get("genres") or [],
        "characters": d.get("characters") or [],
        "relationships": [],
        "favs": d.get("kudos"),
        "follows": d.get("bookmarks"),
        "reviews": d.get("comments"),
        "words": d.get("word_count"),
        "chapters": d.get("chapter_count"),
        "language": d.get("language"),
        "published_at": d.get("published_at"),
        "updated_at": d.get("updated_at"),
        # None when the source said nothing, so it is not mistaken for "not
        # complete". `d.get("status") == "complete"` returned a bare False for
        # both "explicitly still going" and "no status field at all", and now
        # that False actually writes in_progress, conflating them would invent a
        # verdict out of missing data — the precise mistake the `unknown` status
        # exists to avoid.
        "complete": (None if d.get("status") in (None, "", "unknown")
                     else d.get("status") == "complete"),
    }


def parse_ffn_meta(page_text: str) -> dict | None:
    """Pull the metadata line out of an archived FF.net story page.

    The line is a " - " separated list in which almost everything is optional —
    single-chapter stories omit "Chapters:", old ones omit "Words:" and "Favs:",
    and a story with no characters listed simply has no character segment. A
    single regex expecting a fixed shape matched only 2 of 6 real pages, so each
    segment is classified instead:

      * "Rated: X"                     -> rating
      * a bare word in LANGUAGES       -> language
      * all parts in GENRES            -> genres  ("Adventure/Drama")
      * "Label: value"                 -> counts and dates
      * anything else, before counts   -> characters
    """
    txt = re.sub(r"<[^>]+>", " ", page_text)
    txt = txt.replace("&amp;", "&").replace("&#160;", " ").replace("&nbsp;", " ")
    txt = re.sub(r"\s+", " ", txt)

    i = txt.find("Rated:")
    if i < 0:
        return None
    # The line ends at the story id; fall back to a generous slice.
    end = txt.find("id:", i)
    segment = txt[i: end if 0 < end < i + 600 else i + 400]

    out: dict = {"genres": [], "characters": [], "relationships": []}
    seen_counts = False
    for raw in segment.split(" - "):
        part = raw.strip().rstrip(",")
        if not part:
            continue
        if part.lower().startswith("rated:"):
            out["rating"] = part.split(":", 1)[1].replace("Fiction", "").strip()
            continue
        if part in LANGUAGES:
            out["language"] = part
            continue
        m = _LABELLED.match(part)
        if m:
            key = m.group(1).lower()
            val = part.split(":", 1)[1].strip()
            if key in ("reviews", "favs", "follows", "chapters", "words"):
                seen_counts = True
                try:
                    out[key] = int(val.replace(",", ""))
                except ValueError:
                    pass
            elif key in ("published", "updated"):
                dt = _ffn_date(val)
                if dt:
                    out[f"{key}_at"] = dt
            elif key == "status":
                # "Status: Complete". The bare-word test below only catches a
                # segment that STARTS with "complete", so a labelled status was
                # swallowed by this branch and dropped.
                if val.strip().lower().startswith("complete"):
                    out["complete"] = True
            continue
        if part.lower().startswith("complete"):
            out["complete"] = True
            continue
        # Unlabelled: genres if the whole segment resolves to known genres,
        # else characters.
        genres = _classify_genres(part)
        if genres:
            out["genres"] = genres
        elif not seen_counts and not out["characters"]:
            # FF.net marks a romantic pairing by bracketing it:
            #   "[Renamon, Terriermon] Aayla S., Lopmon"
            # means Renamon/Terriermon are shipped and the rest just appear. That
            # bracket is the ONLY relationship data FF.net exposes, and these works
            # otherwise have none at all, so it's worth extracting rather than
            # leaving "[Renamon" as a character name.
            for pair in re.findall(r"\[([^\]]+)\]", part):
                members = [c.strip() for c in pair.split(",") if c.strip()]
                if len(members) >= 2:
                    out.setdefault("relationships", []).append("/".join(members))
            # Replace the brackets with commas, not spaces: "] Aayla S." has no
            # comma before it, so a space would glue it onto the previous name.
            plain = re.sub(r"[\[\]]", ",", part)
            # Characters are comma- or &-separated, and quoted nicknames are common.
            chars = [c.strip() for c in re.split(r",|\s&\s", plain) if c.strip()]
            out["characters"] = [c for c in chars if 1 < len(c) <= 60][:8]

    # Absence of a completion marker on a parsed FF.net metadata line is
    # evidence, not silence.
    #
    # FF.net prints "Status: Complete" on finished works and prints nothing at
    # all on unfinished ones — there is no "Status: In Progress". So having
    # successfully parsed the line, not finding the marker means the work is
    # ongoing. crawlers/ffnet.py has always drawn exactly that conclusion
    # (`_extract_status`); this parser only ever recorded the positive case, so
    # the negative was thrown away and the row stayed `unknown` forever even
    # though we had just read the page.
    #
    # That is why FanFiction.net has 1.29M works marked complete and literally
    # zero marked in-progress, which makes the "In Progress" filter silently
    # AO3-only. Recording False here lets enrichment close that gap over time.
    #
    # Guarded on `seen_counts`: the metadata line always carries counts
    # ("Words: 1,234"), so if none were found we did not really parse a line and
    # must claim nothing either way.
    if seen_counts and "complete" not in out:
        out["complete"] = False
    return out


from wayback_harvest import BACKPRESSURE, Transient  # noqa: E402


def fetch_meta(client: httpx.Client, site_id: str,
               known: tuple[str, str] | None = None) -> dict | None:
    """Find an archived copy of a story page and parse its metadata line.

    Paced by wayback_harvest.BUDGET rather than by this module's own --delay.
    Two loops now talk to archive.org — this one and the AO3 snapshot harvest —
    and each was pacing itself in isolation. That does not compose: individually
    polite, together they drew a steady stream of dropped connections. It is the
    same failure ao3_budget was written for, so it gets the same fix, including
    carrying 429/503 responses back so a refusal to EITHER loop slows both.
    """
    from wayback_harvest import BUDGET, note_response

    if known:
        # Already known from the bulk capture index, so this story costs ONE
        # rate-limited request instead of two. See ffnet_wayback.cdx_prefix_page
        # for why the per-story lookup below is the wrong shape: the same
        # endpoint answers for every story under an id prefix at once, so
        # asking per story spends the entire request budget on discovery that
        # one query could have done for five thousand stories.
        ts, original = known
    else:
        BUDGET.wait()
        try:
            # Every capture, then the NEWEST of them — not `limit=1`.
            #
            # CDX is sorted by urlkey and then by timestamp, so "the first row"
            # is the earliest capture of whichever slug sorts first, and
            # `limit=-1` is the last row of the last slug. Neither is the newest
            # capture of the story, and the difference is most of the value.
            # Measured on story 4985743, eighteen captures spanning 2012-2022:
            #
            #   2012 capture:  favs=None  follows=None  words=None
            #   2022 capture:  favs=1657  follows=1020  words=244923
            #
            # Same story, same parser. FF.net added favourites, follows and
            # word counts to the stats line over the years, and characters did
            # not exist at all on pre-2009 pages — so an old snapshot parses
            # cleanly and yields almost nothing. This backfill exists to supply
            # the engagement signal the ranking has none of, and it was reading
            # the one snapshot per story least likely to carry it.
            #
            # Same request, bigger response.
            resp = client.get(CDX, params={
                "url": f"fanfiction.net/s/{site_id}/1/*",
                "output": "json", "filter": "statuscode:200",
                "fl": "timestamp,original",
            }, timeout=40)
            note_response(resp.status_code)
            rows = resp.json()
        except Exception as e:
            # A refused connection is archive.org saying slow down; a read
            # timeout is one slow capture. Conflating them pinned this budget
            # at its ceiling — see wayback_harvest.note_transport_error.
            from wayback_harvest import note_transport_error
            note_transport_error(e)
            raise Transient(type(e).__name__) from e
        if not rows or len(rows) < 2:
            return None
        ts, original = max(rows[1:], key=lambda r: r[0])[:2]
    BUDGET.wait()
    try:
        page = client.get(f"https://web.archive.org/web/{ts}/{original}",
                          timeout=60, follow_redirects=True)
    except Exception as e:
        # A refused connection is archive.org saying slow down; a read timeout
        # is one slow capture. Conflating them pinned this budget at its
        # ceiling — see wayback_harvest.note_transport_error.
        from wayback_harvest import note_transport_error
        note_transport_error(e)
        raise Transient(type(e).__name__) from e
    note_response(page.status_code)
    if page.status_code in BACKPRESSURE:
        # Being throttled is not an answer about this story. Returning None
        # here made it indistinguishable from "archive.org has no capture",
        # and the caller retires a story it cannot fetch -- so every refusal
        # permanently consumed a queued capture that was perfectly good.
        raise Transient(f"HTTP {page.status_code}")
    if page.status_code != 200:
        return None
    return parse_ffn_meta(page.text)


def _pick_targets(limit: int | None) -> list:
    """Choose which stories to enrich, in a transaction that ends immediately.

    Kept deliberately separate from the fetching. Holding one session open across
    the whole run left a transaction idle-in-transaction for minutes while each
    archive.org request ran, and an open transaction on `stories` blocks any
    ACCESS EXCLUSIVE lock — so init_db()'s `ALTER TABLE stories ADD COLUMN IF NOT
    EXISTS` waited behind it on every API start, the lifespan never completed, and
    the whole app returned 500 until the transaction was killed.

    Targets are chosen by how much is missing AND how likely the work is to be
    seen, via gap_filler — not just "longest first" as before. Length is a crude
    stand-in for readership and says nothing about how much a row actually
    lacks, so a 200k-word story missing only its language outranked a widely-read
    one with no summary, characters or dates at all.

    Rows already checked and still empty fall to the back through the
    crawled_at tiebreak, so the queue keeps advancing.
    """
    from gap_filler import find_gaps
    from sqlalchemy import text as sql_text

    want = limit or 1000
    out: list = []
    with db_session() as db:
        # Stories archive.org demonstrably HOLDS come first.
        #
        # Measured, and the gap is not marginal. Ten stories drawn this way
        # yielded characters for six; the eight the gap score was drawing
        # yielded characters for none, and could not have, because they are
        # 2002-2005 works whose archived pages have no character field at all.
        #
        # Two independent reasons this works. The capture index is a fact about
        # what exists, so none of these requests is spent discovering that
        # there is nothing to fetch -- that was about 31% of them. And the
        # captures are modern: of the first 378,000 indexed, 219,653 are from
        # 2018 and 123,316 from 2015, against barely 180 predating 2011. Every
        # one of those renders the stats line that carries characters,
        # favourites, follows and a word count.
        #
        # Ordered by staleness first for the reason gap_filler now is: a queue
        # that re-draws its own head does not advance. Then by what the story
        # is missing, so the emptiest capture-backed rows go first.
        # A bounded pool off the head of the capture queue, then ranked.
        #
        # Joining every known capture to `stories` and sorting the result cost
        # 911,000 planner units at 589,000 captures and tripped the 60s
        # statement timeout mid-pass; the index is heading for several million,
        # so that shape was never going to hold. Reading the head of a partial
        # index and stopping is O(batch) no matter how large the index gets.
        #
        # Newest captures first, because a 2018 snapshot carries the whole
        # stats line and a 2012 one often does not. Then ranked by readership
        # WITHIN the pool -- an approximation of the global ordering, but this
        # gap never closes, so the question is only ever which few hundred to
        # fetch next, and the pool is drawn from the best captures we hold.
        rows = db.execute(sql_text("""
            WITH cand AS (
                SELECT site_id, snapshot_ts, original
                  FROM ffnet_captures
                 WHERE done_at IS NULL AND original IS NOT NULL
                 ORDER BY snapshot_ts DESC
                 LIMIT :pool
            )
            SELECT s.id, s.site_id, c.snapshot_ts, c.original
              FROM cand c
              JOIN stories s ON s.site = 'ffnet' AND s.site_id = c.site_id::text
             WHERE cardinality(s.characters) = 0
             ORDER BY (COALESCE(s.kudos,0) + COALESCE(s.hits,0)
                       + COALESCE(s.favourites,0)) DESC,
                      c.snapshot_ts DESC
             LIMIT :lim
        """), {"lim": want, "pool": max(want * 20, 2000)}).fetchall()
        out = [(r[0], r[1], 0, (r[2], r[3])) for r in rows]

        # Only if the capture index cannot fill the batch. It is still being
        # built -- the prefix walk covers ids 1000-9999 over several days -- so
        # early on this is most of the batch and later it should be none of it.
        if len(out) < want:
            gaps = find_gaps(db, "ffnet", limit=want - len(out))
            have = {str(r[1]) for r in out}
            ids = [int(r["site_id"]) for r in gaps
                   if str(r["site_id"]).isdigit()]
            known: dict[str, tuple[str, str]] = {}
            if ids:
                for sid, ts, orig in db.execute(sql_text(
                        "SELECT site_id, snapshot_ts, original FROM ffnet_captures "
                        "WHERE site_id = ANY(:ids)"), {"ids": ids}):
                    if orig:
                        known[str(sid)] = (ts, orig)
            out += [(r["id"], r["site_id"], r["gap_score"],
                     known.get(str(r["site_id"])))
                    for r in gaps if str(r["site_id"]) not in have]
    return out


def run(limit: int | None, dry_run: bool, delay: float, batch: int,
        max_seconds: float | None = None) -> None:
    """One enrichment pass.

    `max_seconds` bounds the pass by WALL CLOCK, not by story count, and that is
    what makes the background loop work at all. archive.org signals throttling
    by refusing connections rather than by returning 429, and the budget backs
    off to 600s a request in response. At that rate a 200-story pass takes 33
    hours, so the worker's 30-minute loop simply never came round again — the
    last pass started at 22:56 and was still going nine hours later, which is
    why FF.net character coverage sat at 1.7% looking like a dead feature.

    Stopping early costs nothing: _pick_targets only ever selects stories that
    still have no characters, so the next pass resumes on what is left.
    """
    import time as _time
    deadline = (_time.monotonic() + max_seconds) if max_seconds else None
    updated = missing = failed = from_fichub = refused = 0
    rows = _pick_targets(limit)
    log.info(f"{len(rows)} FF.net stories to enrich")
    pending: list[tuple] = []          # (story_id, parsed metadata) awaiting a write
    attempted: list = []               # every story we looked at, found or not

    with httpx.Client(headers=UA) as client:
        for n, (sid, site_id, wc, known) in enumerate(rows, 1):
            if deadline and _time.monotonic() > deadline:
                log.info(f"  time budget reached after {n - 1} stories — "
                         f"stopping so the loop can come round")
                break
            try:
                meta = fetch_meta(client, site_id, known)
            except Transient as e:
                # archive.org refused us. That says nothing about this story,
                # so it must stay queued -- recording it as attempted would
                # retire a capture we never actually read.
                refused += 1
                continue
            attempted.append(sid)
            if not meta and _FICHUB_FALLBACK:
                # ~31% of works have no usable Wayback capture (no_snapshot=62
                # of 200 in a measured pass), and those requests were simply
                # spent for nothing. FicHub resolves FFN URLs directly — it does
                # the Cloudflare work we cannot — so it recovers exactly the
                # ones archive.org never captured.
                #
                # A fallback rather than the primary source on purpose: bulk
                # sweeps of millions of rows belong on the Internet Archive,
                # which is built for that, not on one volunteer's server. This
                # only fires where Wayback has already failed.
                fh = _fichub_fallback(site_id)
                if fh:
                    meta = fh
                    from_fichub += 1
            if not meta:
                missing += 1
            elif any(meta.get(k) for k in
                     ("genres", "characters", "relationships", "favs", "follows", "reviews")):
                pending.append((sid, meta))
            else:
                failed += 1

            if dry_run and n <= 10 and meta:
                log.info(f"  {site_id}: genres={meta.get('genres')} "
                         f"chars={meta.get('characters')} ships={meta.get('relationships')} "
                         f"favs={meta.get('favs')}")

            # Write in short bursts. The database session is only open for the
            # write itself, never across an archive.org request.
            if not dry_run and len(attempted) >= batch:
                updated += _write_batch(pending)
                pending.clear()
                _mark_attempted(attempted)
                attempted.clear()
                log.info(f"  {n}/{len(rows)} — {updated} enriched, {missing} no snapshot")

            # Pacing lives in the shared archive.org budget now (see
            # fetch_meta), which already blocked for the right interval before
            # each request. Sleeping again here would stack a second delay on
            # top of it and slow the run for no additional politeness. Kept
            # honoured only if a caller asks for extra spacing explicitly.
            if delay:
                time.sleep(delay)

    if dry_run:
        log.info(f"Dry run — {len(pending)} would be enriched, {missing} had no snapshot.")
        return

    if pending:
        updated += _write_batch(pending)
    if attempted:
        _mark_attempted(attempted)
    log.info(f"DONE — enriched={updated} no_snapshot={missing} "
             f"refused={refused} "
             f"via_fichub={from_fichub} unparseable={failed}")


def _mark_attempted(ids: list) -> None:
    """Record that we looked, so the queue can move on.

    This is what kept FF.net character coverage frozen at 108,468 through every
    other fix in this file. `find_gaps` orders by how much a row is missing and
    breaks ties on `crawled_at ASC NULLS FIRST` -- and nothing in this module
    ever wrote `crawled_at`, so the tiebreak had nothing to break with and every
    pass drew the same rows again.

    Which rows made it terminal. The worst gap scores belong to the OLDEST
    stories, the ones missing characters, word counts, favourites and follows
    all at once -- and FF.net did not have a character field before about 2009,
    so those fields are not missing from our copy, they never existed. Measured
    on a live selection: ids 1625, 202864, 742503, 905278, 1383206, 1470891,
    every one a 2002-2005 story, every one parsing cleanly to characters=[].
    The queue was pinned on precisely the stories that can never satisfy it,
    re-fetching them for ever, and a pass could report enriched=14 while the
    number it was trying to move did not change by one.

    An attempt is information even when it finds nothing, so it is recorded
    like one. `crawled_at` is the honest column for it -- we did just look.
    """
    if not ids:
        return
    from sqlalchemy import text as sql_text
    try:
        with db_session() as db:
            # Give up rather than queue. `stories` is under constant write load
            # and the weekly popularity pass takes row locks across most of the
            # table for the better part of an hour; a bookkeeping stamp must
            # never be the thing that wedges the harvest behind it. Losing a
            # stamp costs one repeated fetch next pass. Waiting costs the pass.
            db.execute(sql_text("SET LOCAL lock_timeout = '5s'"))
            db.execute(sql_text(
                "UPDATE stories SET crawled_at = now() WHERE id = ANY(:ids)"),
                {"ids": list(ids)})
            # And on the capture queue itself, which is what selection reads.
            # Without this the same head of the queue comes back every pass --
            # the exact failure the crawled_at stamp was added to fix, one
            # table along.
            db.execute(sql_text("""
                UPDATE ffnet_captures c SET done_at = now()
                  FROM stories s
                 WHERE s.id = ANY(:ids)
                   AND s.site = 'ffnet' AND c.site_id::text = s.site_id
            """), {"ids": list(ids)})
            db.commit()
    except Exception as e:
        log.info(f"  attempt stamp skipped ({type(e).__name__}); "
                 f"{len(ids)} stories may be re-selected")


def _write_batch(items: list[tuple]) -> int:
    """Apply a batch of parsed metadata. Opens and closes its own short session."""
    written = 0
    with db_session() as db:
        for sid, meta in items:
            story = db.query(Story).filter(Story.id == sid).first()
            if not story:
                continue
            if meta.get("genres"):
                story.genres = meta["genres"]
                # FF.net genres ARE the content tags for these works — they are
                # what a reader filters by, so surface them as tags too.
                existing = list(story.tags or [])
                for g in meta["genres"]:
                    if g not in existing:
                        existing.append(g)
                story.tags = existing
            if meta.get("characters"):
                story.characters = meta["characters"]
            if meta.get("relationships"):
                story.relationships = meta["relationships"]
            # Engagement: the index has almost none, so this is the only real
            # popularity signal available for ranking.
            if meta.get("favs") is not None:
                story.kudos = meta["favs"]
            if meta.get("follows") is not None:
                story.bookmarks = meta["follows"]
            if meta.get("reviews") is not None:
                story.comments = meta["reviews"]

            # Dates. The FFN dump carried none at all, so before this every one
            # of 6,572,195 rows sorted and filtered as undated.
            if meta.get("published_at") and story.published_at is None:
                story.published_at = meta["published_at"]
            upd = meta.get("updated_at")
            if upd and (story.updated_at is None or upd > story.updated_at):
                story.updated_at = upd

            # "Complete" on the page IS evidence, unlike the dump's silence that
            # everything else defaults to `unknown` for.
            #
            # Three states, not two. `complete` is now True, False or absent, and
            # they mean different things: absent is "we learned nothing", False
            # is "the page said it is still going". Only the first should leave
            # the row alone.
            #
            # in_progress is written only over `unknown`, never over an existing
            # verdict. A work that was recorded complete and later shows an
            # archived snapshot from before it finished must not be dragged
            # backwards by the older evidence.
            if meta.get("complete") and story.status != StatusEnum.complete:
                story.status = StatusEnum.complete
            elif (meta.get("complete") is False
                  and story.status in (None, StatusEnum.unknown)):
                story.status = StatusEnum.in_progress

            # Parsed already and previously discarded; only fills gaps.
            if meta.get("words") and not (story.word_count or 0):
                story.word_count = meta["words"]
            if meta.get("chapters") and (story.chapter_count or 0) < meta["chapters"]:
                story.chapter_count = meta["chapters"]

            written += 1
        db.commit()
    return written


def main():
    ap = argparse.ArgumentParser(description="Backfill FF.net metadata via Wayback")
    ap.add_argument("--limit", type=int, help="How many stories to attempt")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between requests — be kind to archive.org")
    ap.add_argument("--batch", type=int, default=25, help="Commit every N stories")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay, args.batch)


if __name__ == "__main__":
    main()
