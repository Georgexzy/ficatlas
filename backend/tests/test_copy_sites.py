"""A work's other copies: named the same in Python and SQL, kept by trigger,
and found by a site filter. See copy_sites.py."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

import copy_sites
from api.search import router as search_router
from db.session import get_db

URLS = [
    ["https://www.fanfiction.net/s/123/1/"],
    ["https://archiveofourown.org/works/9", "http://m.fanfiction.net/s/5"],
    ["seed://janelleshane/x", "&#039;https:"],
    ["https://www.example.org/x"],
    [],
]


def test_python_names_the_archives():
    assert copy_sites.copy_sites(URLS[0]) == ["ffnet"]
    assert copy_sites.copy_sites(URLS[1]) == ["ao3", "ffnet"]
    assert copy_sites.copy_sites(URLS[2]) is None
    assert copy_sites.copy_sites([]) is None


def test_no_colon_words_in_the_sql():
    # text() binds ":word" even inside a regex or a comment.
    import re
    assert not re.findall(r"(?<![:\w]):[A-Za-z_]\w*", copy_sites.FUNCTION_SQL + copy_sites.TRIGGER_SQL)


def test_sql_agrees_with_python(db):
    for urls in URLS:
        got = db.execute(text("SELECT fic_copy_sites(CAST(:u AS text[]))"), {"u": urls}).scalar()
        assert got == copy_sites.copy_sites(urls), urls


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_a_site_filter_finds_a_work_through_its_other_copy(db, client):
    n = uuid.uuid4().hex
    sid = db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, tags, cross_post_urls)
        VALUES ('ao3', :sid, :url, 'Zebra Copy', 'A', ARRAY['Quagga Rodeo'],
                ARRAY['https://www.fanfiction.net/s/777/1/'])
        RETURNING id
    """), {"sid": n[:12], "url": f"https://archiveofourown.org/works/{int(n[:6], 16)}"}).scalar()
    db.commit()
    assert db.execute(text("SELECT copy_sites FROM stories WHERE id = :i"),
                      {"i": sid}).scalar() == ["ffnet"]       # the trigger set it
    for sites in ("ffnet", "ao3", "ao3,ffnet,fictionalley"):
        r = client.get("/api/search", params={"q": 'tag:"Quagga Rodeo"', "sites": sites})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1, sites
    r = client.get("/api/search", params={"q": 'tag:"Quagga Rodeo"', "sites": "fictionalley"})
    assert r.status_code == 200 and r.json()["total"] == 0
