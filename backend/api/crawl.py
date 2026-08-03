"""Crawl management endpoints"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from db.session import get_db
from api.auth import require_admin
from models.story import CrawlJob, SiteEnum
from crawlers.ao3 import AO3Crawler
from crawlers.ffnet import FFNetCrawler
from datetime import datetime

router = APIRouter()

CRAWLERS = {
    "ao3": AO3Crawler,
    "ffnet": FFNetCrawler,
}


@router.post("/trigger/{site}")
async def trigger_crawl(
    site: str,
    job_type: str = "incremental",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Start a crawl. Requires an account.

    This was the one mutating endpoint with no guard on it. Anyone who could
    reach the app could queue unlimited crawls — not just free compute, but
    outbound requests to AO3 and FF.net from the host's IP, which is exactly
    what gets an address rate-limited or blocked for everyone using it. Every
    other discover/import endpoint already required an account; this one was
    simply missed.
    """
    if site not in CRAWLERS:
        return {"error": f"No crawler for site: {site}"}

    job = CrawlJob(site=SiteEnum(site), job_type=job_type, status="pending")
    db.add(job)
    db.commit()

    background_tasks.add_task(_run_crawl, str(job.id), site, job_type)
    return {"job_id": str(job.id), "status": "queued"}


@router.get("/jobs")
async def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(20).all()
    return [_job_dict(j) for j in jobs]


@router.get("/schedule")
async def get_schedule(db: Session = Depends(get_db)):
    """Returns schedule info + most recent completed job per site."""
    from scheduler import get_schedule_status
    schedule = get_schedule_status()

    # Attach last completed job stats per site
    for site in ["ao3", "ffnet"]:
        last = (
            db.query(CrawlJob)
            .filter(CrawlJob.site == SiteEnum(site), CrawlJob.status == "done")
            .order_by(CrawlJob.finished_at.desc())
            .first()
        )
        running = (
            db.query(CrawlJob)
            .filter(CrawlJob.site == SiteEnum(site), CrawlJob.status.in_(["running", "pending"]))
            .order_by(CrawlJob.created_at.desc())
            .first()
        )
        schedule[site]["last_job"] = _job_dict(last) if last else None
        schedule[site]["active_job"] = _job_dict(running) if running else None

    return schedule


async def _run_crawl(job_id: str, site: str, job_type: str):
    from db.session import db_session
    import uuid
    crawler_cls = CRAWLERS[site]

    with db_session() as db:
        job = db.query(CrawlJob).filter(CrawlJob.id == uuid.UUID(job_id)).first()
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()

    try:
        crawler = crawler_cls()
        stats = await crawler.run(job_type=job_type)
        with db_session() as db:
            job = db.query(CrawlJob).filter(CrawlJob.id == uuid.UUID(job_id)).first()
            if job:
                job.status = "done"
                job.stories_found = stats.get("found", 0)
                job.stories_new = stats.get("new", 0)
                job.stories_updated = stats.get("updated", 0)
                job.finished_at = datetime.utcnow()
    except Exception as e:
        with db_session() as db:
            job = db.query(CrawlJob).filter(CrawlJob.id == uuid.UUID(job_id)).first()
            if job:
                job.status = "failed"
                job.error = str(e)
                job.finished_at = datetime.utcnow()


def _job_dict(job: CrawlJob | None) -> dict | None:
    if not job:
        return None
    return {
        "id": str(job.id),
        "site": job.site.value if job.site else None,
        "job_type": job.job_type,
        "status": job.status,
        "stories_found": job.stories_found,
        "stories_new": job.stories_new,
        "stories_updated": job.stories_updated,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
