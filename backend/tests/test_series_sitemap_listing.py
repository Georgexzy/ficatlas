"""What `/api/stories/series` promises the sitemap.

Two callers, two different needs, one endpoint. The /series browse page wants
names and work_count ranked by popularity (with_names=true); the sitemap wants
a flat id+stamp list. The flat list's `limit` means THE NEWEST N, because the
sitemap's consumer keeps its own newest 10,000 (frontend/lib/sitemapData.ts,
SERIES_SITEMAP_LIMIT) and an unbounded listing ships 355,138 rows a day for
10,000 of them to survive.

The property worth testing is the ORDERING under `limit`: if it were "first N
by slug", the site's series surface would silently be whichever UUIDs sort
early — a set that moves on every insert, and not a set anyone chose. UUIDs
here are assigned as small integers so "sorts early" and "was created early"
are on opposite ends of the table and the two readings cannot be confused for
each other.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.stories import router as stories_router
from db.session import get_db

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(stories_router, prefix="/api/stories")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _seed(db, n, *, with_names=False):
    """n series with id 1..n (so slug order is creation order in reverse of the
    timestamps we assign) and created_at rising with the id."""
    for i in range(1, n + 1):
        db.execute(text("""
            INSERT INTO series (id, name, author, work_count, total_words, created_at)
            VALUES (:id, :name, 'Test Author', :wc, 1000, :at)
        """), {
            "id": uuid.UUID(int=i),
            "name": f"Series {i:03d}",
            "wc": (i if with_names else 0),
            "at": BASE + timedelta(days=i),
        })
    db.commit()


class TestFlatListingIsNewestFirst:
    def test_limit_returns_the_newest_n_not_the_first_n_by_slug(self, client, db):
        _seed(db, 8)   # ids 1..8, created_at rising with the id
        got = client.get("/api/stories/series?limit=3").json()
        assert [s["slug"] for s in got] == [
            str(uuid.UUID(int=i)) for i in (8, 7, 6)
        ]
        # The point of the assertion above: a slug window would have returned
        # ids 1, 2, 3 — the OLDEST three — because these UUIDs sort in creation
        # order.
        assert {s["slug"] for s in got}.isdisjoint(
            {str(uuid.UUID(int=i)) for i in (1, 2, 3)}
        )

    def test_created_at_reaches_the_response_as_content_at(self, client, db):
        _seed(db, 2)
        got = {s["slug"]: s["content_at"] for s in client.get("/api/stories/series").json()}
        assert got[str(uuid.UUID(int=1))] == (BASE + timedelta(days=1)).isoformat()
        assert got[str(uuid.UUID(int=2))] == (BASE + timedelta(days=2)).isoformat()

    def test_without_a_limit_it_is_still_the_whole_flat_list_in_slug_order(self, client, db):
        """The full listing is the other half of the contract — a cap that
        applied unconditionally would shrink the sitemap to whatever head the
        API chose, with no way for the caller to ask for the rest."""
        _seed(db, 5)
        got = client.get("/api/stories/series").json()
        assert [s["slug"] for s in got] == [
            str(uuid.UUID(int=i)) for i in range(1, 6)
        ]


class TestBrowseListingUntouched:
    def test_with_names_still_ranks_by_work_count(self, client, db):
        """The browse payload has its own ordering and its own meaning of
        `limit`; the sitemap change must not have leaked into it."""
        _seed(db, 5, with_names=True)
        got = client.get("/api/stories/series?with_names=true&limit=3").json()
        assert [s["name"] for s in got] == ["Series 005", "Series 004", "Series 003"]
        assert all(s["author"] == "Test Author" for s in got)
