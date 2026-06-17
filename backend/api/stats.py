"""Stats endpoint — per-site counts, totals, last-updated info"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from db.session import get_db
from models.story import Story

router = APIRouter()

@router.get("/sites")
async def site_stats(db: Session = Depends(get_db)):
    rows = (db.query(Story.site, func.count(Story.id), func.max(Story.indexed_at))
            .group_by(Story.site).all())
    return [
        {
            "site":  r[0].value if hasattr(r[0], "value") else str(r[0]),
            "count": r[1],
            "last_indexed": r[2].isoformat() if r[2] else None,
        }
        for r in rows
    ]

@router.get("/totals")
async def total_stats(db: Session = Depends(get_db)):
    stories = db.query(func.count(Story.id)).scalar() or 0
    hosted  = db.query(func.count(Story.id)).filter(Story.is_hosted == True).scalar() or 0
    total_words = db.query(func.sum(Story.word_count)).scalar() or 0
    # DLP-curated story count (any row with the dlp_library tag in its tags array)
    dlp = db.query(func.count(Story.id)).filter(
        Story.tags.any("dlp_library")  # type: ignore[arg-type]
    ).scalar() or 0
    # HPFFA archive count (imported via AO3 Open Doors)
    hpffa = db.query(func.count(Story.id)).filter(
        Story.tags.any("hpffa_archive")  # type: ignore[arg-type]
    ).scalar() or 0
    return {"stories": stories, "hosted": hosted, "total_words": int(total_words),
            "dlp": dlp, "hpffa": hpffa}
