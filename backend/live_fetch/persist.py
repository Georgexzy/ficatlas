"""Persist live-fetched results into the DB so the index grows over time."""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models.story import Story, SiteEnum, RatingEnum, StatusEnum

log = logging.getLogger(__name__)

_RATING_MAP = {
    "G":  RatingEnum.general,
    "T":  RatingEnum.teen,
    "M":  RatingEnum.mature,
    "E":  RatingEnum.explicit,
    "NR": RatingEnum.not_rated,
}

_STATUS_MAP = {
    "complete":    StatusEnum.complete,
    "in_progress": StatusEnum.in_progress,
}


def persist_live_results(db: Session, live_results: list[dict]) -> int:
    """
    Save live-fetched stories to the DB if they aren't already there.
    Commits each row individually so a single bad row doesn't roll back the batch.
    Returns the count of rows that actually committed.
    """
    if not live_results:
        return 0

    # Pre-fetch existing URLs in one query — cheap and avoids per-row SELECT roundtrips
    urls = [d.get("url") for d in live_results if d.get("url")]
    existing_urls: set[str] = set()
    if urls:
        rows = db.query(Story.url).filter(Story.url.in_(urls)).all()
        existing_urls = {r[0] for r in rows}

    saved = 0
    enriched = 0
    skipped_existing = 0
    failed = 0
    merged_crosspost = 0

    from live_fetch.crosspost import find_crosspost_for

    for d in live_results:
        url = d.get("url")
        if not url:
            failed += 1
            continue
        if url in existing_urls:
            # Already indexed — but the live blurb may carry fields the bulk
            # import never had. AO3's metadata dump has NO summary field at all,
            # so 13M of our AO3 rows have none, while every AO3 listing blurb
            # includes one. Skipping outright threw that away on every fetch.
            #
            # Only ever FILLS IN what is missing; never overwrites existing data
            # with the blurb's version.
            if _enrich_existing(db, url, d):
                enriched += 1
            else:
                skipped_existing += 1
            continue

        # Cross-post check: does this same work already exist from another site?
        # If so, just record this URL as an alternate link rather than making a
        # duplicate row. Conservative title+author match (see crosspost.py).
        try:
            existing_work = find_crosspost_for(
                db, d.get("title") or "", d.get("author") or "", exclude_url=url
            )
        except Exception:
            existing_work = None
        if existing_work is not None:
            try:
                alts = set(existing_work.cross_post_urls or [])
                if url not in alts and url != existing_work.url:
                    alts.add(url)
                    existing_work.cross_post_urls = sorted(alts)
                    existing_work.is_crossover = existing_work.is_crossover or False
                    db.commit()
                    merged_crosspost += 1
                existing_urls.add(url)
                continue
            except Exception:
                db.rollback()
        try:
            site_id = d["id"].replace("live_ao3_", "")
            updated_at = None
            if d.get("updated_at"):
                try:
                    updated_at = datetime.fromisoformat(d["updated_at"])
                except Exception:
                    pass

            story = Story(
                site=SiteEnum.ao3,
                site_id=site_id,
                url=url,
                title=(d.get("title") or "Untitled")[:500],
                author=(d.get("author") or "Anonymous")[:200],
                author_url=d.get("author_url"),
                summary=d.get("summary"),
                language=d.get("language") or "English",
                rating=_RATING_MAP.get(d.get("rating") or "NR", RatingEnum.not_rated),
                status=_STATUS_MAP.get(d.get("status") or "unknown", StatusEnum.unknown),
                word_count=d.get("word_count") or 0,
                chapter_count=d.get("chapter_count") or 1,
                chapter_count_total=d.get("chapter_count_total"),
                kudos=d.get("kudos") or 0,
                hits=d.get("hits") or 0,
                bookmarks=d.get("bookmarks") or 0,
                comments=d.get("comments") or 0,
                fandoms=d.get("fandoms") or [],
                characters=d.get("characters") or [],
                relationships=d.get("relationships") or [],
                tags=d.get("tags") or [],
                warnings=d.get("warnings") or [],
                categories=d.get("categories") or [],
                genres=[],
                is_crossover=len(d.get("fandoms", [])) > 1,
                is_hosted=False,
                published_at=None,
                updated_at=updated_at,
            )
            db.add(story)
            db.commit()      # commit each row individually
            saved += 1
            # add to existing_urls so duplicates within the same batch are skipped
            existing_urls.add(url)
        except IntegrityError:
            db.rollback()
            skipped_existing += 1  # most likely a duplicate site_id from elsewhere
            existing_urls.add(url)
        except Exception as e:
            db.rollback()
            failed += 1
            log.warning(f"Skip live persist for {url}: {e}")

    if saved or failed or skipped_existing or merged_crosspost or enriched:
        log.info(
            f"persist_live_results: saved={saved} enriched={enriched} "
            f"already_indexed={skipped_existing} cross_post_merged={merged_crosspost} "
            f"failed={failed} (of {len(live_results)} candidates)"
        )

    return saved


def _enrich_existing(db: Session, url: str, d: dict) -> bool:
    """Refresh an already-indexed story from a freshly fetched blurb.

    Two jobs, and the second one is why this exists at all.

    Filling gaps: AO3's metadata dump carries no summary field, so 13M rows have
    none while every listing blurb has one. Missing facets and engagement counts
    are filled the same way, and never overwritten — a blurb must not downgrade
    richer data from a full import.

    Tracking updates: a work is otherwise frozen at the moment it was imported.
    A work-in-progress that has since gained ten chapters kept its original
    word_count, chapter_count and status forever, because the previous code
    skipped any URL it already had. Fanfiction is mutable — that is the whole
    point of a WIP — so mutable fields move forward when the blurb shows the work
    has progressed.

    "Forward" only: chapters and words may grow, a WIP may become complete, and
    engagement counts only rise. A blurb reporting *less* is treated as a partial
    or stale render and ignored, rather than deleting data we already hold.
    """
    try:
        story = db.query(Story).filter(Story.url == url).first()
        if story is None:
            return False

        changed = False

        # ── Repair truncated titles ────────────────────────────────────────
        # The AO3 metadata dump ships titles cut off mid-phrase. Verified
        # against AO3 itself:
        #   dump "Harry Potter and"  -> "Harry Potter and Homosexual Rights
        #                                Feat. Severus Snape"
        #   dump "The Masochism of"  -> "The Masochism of Self-Defence"
        # 654,523 AO3 rows end on a dangling "and/of/the/with", and those are
        # only the ones detectable by inspection.
        #
        # A blurb title is taken only when it EXTENDS what we hold — the stored
        # value must be a prefix of it — so a differently-punctuated or unrelated
        # title can never overwrite a good one.
        new_title = (d.get("title") or "").strip()
        old_title = (story.title or "").strip()
        if (new_title and len(new_title) > len(old_title)
                and new_title.lower().startswith(old_title.lower())):
            story.title = new_title[:500]
            changed = True

        # ── Fill gaps: only where we hold nothing ──────────────────────────
        if not (story.summary or "").strip() and (d.get("summary") or "").strip():
            story.summary = d["summary"]
            changed = True

        for attr, key in (("tags", "tags"), ("characters", "characters"),
                          ("relationships", "relationships"), ("fandoms", "fandoms"),
                          ("warnings", "warnings"), ("categories", "categories")):
            if not (getattr(story, attr) or []) and (d.get(key) or []):
                setattr(story, attr, d[key])
                changed = True

        # ── Track updates: mutable fields move forward ─────────────────────
        for attr, key in (("chapter_count", "chapter_count"),
                          ("word_count", "word_count"),
                          ("kudos", "kudos"), ("hits", "hits"),
                          ("bookmarks", "bookmarks"), ("comments", "comments")):
            new_val = d.get(key) or 0
            if new_val > (getattr(story, attr) or 0):
                setattr(story, attr, new_val)
                changed = True

        # A WIP becoming complete is real news; the reverse is not, since AO3
        # only ever moves a work in that direction.
        if d.get("status") == "complete" and story.status != StatusEnum.complete:
            story.status = StatusEnum.complete
            changed = True

        # The work's own last-updated date, when the blurb reports a newer one.
        blurb_updated = None
        if d.get("updated_at"):
            try:
                blurb_updated = datetime.fromisoformat(d["updated_at"])
            except Exception:
                blurb_updated = None
        if blurb_updated is not None:
            current = story.updated_at
            if current is None or (
                current.replace(tzinfo=None) < blurb_updated.replace(tzinfo=None)
            ):
                story.updated_at = blurb_updated
                changed = True

        # crawled_at means "when did we last SEE this work". It was set once at
        # insert and never touched again, so there was no way to tell a row
        # verified minutes ago from one imported months ago — and therefore no
        # way to prioritise what to re-check.
        story.crawled_at = datetime.now(timezone.utc)

        db.commit()
        return changed
    except Exception as e:
        db.rollback()
        log.warning(f"refresh existing {url} failed: {e}")
        return False
