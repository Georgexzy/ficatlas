from reader_import_jobs import create, read, update
import asyncio
import pytest
from fastapi import HTTPException
from models.user import User
from api import library as library_api


def test_import_job_state_is_shared_and_owner_scoped(db):
    job_id, _ = create(db, "reader-1")
    assert read(db, job_id, "reader-1")["status"] == "running"
    assert read(db, job_id, "reader-2") is None

    update(db, job_id, status="done", result={"id": "story-1"})
    state = read(db, job_id, "reader-1")
    assert state["status"] == "done"
    assert state["result"]["id"] == "story-1"
    assert "user_id" not in state


def test_import_job_endpoint_requires_owner_for_status(monkeypatch, db):
    owner = User(username="job_owner", password_hash="x")
    other = User(username="job_other", password_hash="x")
    db.add_all([owner, other])
    db.commit()
    queued = []
    monkeypatch.setattr("live_fetch.jobs.run_in_background",
                        lambda fn: queued.append(fn))

    result = asyncio.run(library_api.import_url_job(
        url="https://archiveofourown.org/works/123456",
        db=db, viewer=owner))
    assert result["job_id"]
    assert len(queued) == 1
    status = library_api.import_url_job_status(result["job_id"], db=db, viewer=owner)
    assert status["status"] == "running"
    with pytest.raises(HTTPException) as error:
        library_api.import_url_job_status(result["job_id"], db=db, viewer=other)
    assert error.value.status_code == 404
