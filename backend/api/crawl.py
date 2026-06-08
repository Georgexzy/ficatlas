"""Crawl management endpoints"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from db.session import get_db
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
):
    if site not in CRAWLERS:
        return {"error": f"No crawler for site: {site}"}

    job = CrawlJob(site=SiteEnum(site), job_type=job_type, status="pending")
    db.add(job)
    db.commit()

    background_tasks.add_task(_run_crawl, str(job.id), site, job_type)
    return {"job_id": str(job.id), "status": "queued"}


@router.get("/jobs")
async def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(50).all()
    return jobs


async def _run_crawl(job_id: str, site: str, job_type: str):
    from db.session import db_session
    crawler_cls = CRAWLERS[site]
    with db_session() as db:
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        job.status = "running"
        job.started_at = datetime.utcnow()

    try:
        crawler = crawler_cls()
        stats = await crawler.run(job_type=job_type)
        with db_session() as db:
            job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
            job.status = "done"
            job.stories_found = stats.get("found", 0)
            job.stories_new = stats.get("new", 0)
            job.stories_updated = stats.get("updated", 0)
            job.finished_at = datetime.utcnow()
    except Exception as e:
        with db_session() as db:
            job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
            job.status = "failed"
            job.error = str(e)
            job.finished_at = datetime.utcnow()
