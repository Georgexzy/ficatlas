"""Persist live-fetched results into the DB so the index grows over time."""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
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
    Returns the count of newly-inserted stories.
    """
    if not live_results:
        return 0

    inserted = 0
    for d in live_results:
        try:
            site_id = d["id"].replace("live_ao3_", "")
            url     = d["url"]

            # Skip if URL already exists
            existing = db.query(Story.id).filter(Story.url == url).first()
            if existing:
                continue

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
                title=d.get("title") or "Untitled",
                author=d.get("author") or "Anonymous",
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
            inserted += 1
        except Exception as e:
            log.warning(f"Skip live persist for {d.get('url')}: {e}")
            db.rollback()
            continue

    if inserted:
        try:
            db.commit()
            log.info(f"Persisted {inserted} new live AO3 results into the index.")
        except Exception as e:
            log.warning(f"Commit failed for live persistence: {e}")
            db.rollback()
            return 0

    return inserted
