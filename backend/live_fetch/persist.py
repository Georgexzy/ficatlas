"""Persist live-fetched results into the DB so the index grows over time."""
import logging
from datetime import datetime
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
    skipped_existing = 0
    failed = 0

    for d in live_results:
        url = d.get("url")
        if not url:
            failed += 1
            continue
        if url in existing_urls:
            skipped_existing += 1
            continue

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
                status=_STATUS_MAP.get(d.get("status") or "in_progress", StatusEnum.in_progress),
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

    if saved or failed or skipped_existing:
        log.info(
            f"persist_live_results: saved={saved} "
            f"already_indexed={skipped_existing} failed={failed} "
            f"(of {len(live_results)} candidates)"
        )

    return saved
