"""When a hub page's `content_at` is allowed to move.

`content_at` is the sitemap's <lastmod> for all 12,475 URLs, and it is the one
value on a hub page that has to be TRUE rather than merely recent. Google is
explicit that it uses lastmod only when it is consistently accurate, so a site
that stamps every hub every night is no better off than one that never stamps
at all — it has taught the crawler to ignore the field.

This has broken once, in the direction that looks like diligence: 52% of ship
hubs were stamped on a typical day, every day, because two of the three tests
were too sensitive.

So the rule is asserted against the real expression, not a restatement of it.
`content_at_sql` is imported from `hub_build`, which is the whole point — a test
that copied the SQL would keep passing while the SQL regressed, which is exactly
how `content_gates.py` and `api/search.py` came to hold two different lists of
what counts as adult content before `gate_terms.py` existed to stop it.

The upsert runs for real against the test database, because the interesting
failures here are semantic: `IS DISTINCT FROM` against NULL, integer division in
`work_count / 100`, and array slicing on `top_ids`.
"""
import pytest
from sqlalchemy import text

from hub_build import content_at_sql

TABLE = "fandom_hubs"
SLUG = "harry-potter"

# The upsert exactly as build_groups issues it, with the real expression.
UPSERT = f"""
    INSERT INTO {TABLE} (slug, name, variants, work_count, top_ids,
                         top_by_site, site_counts, built_at, content_at)
    VALUES (:slug, :name, '{{}}'::text[], :wc, :top,
            '{{}}'::jsonb, '{{}}'::jsonb, now(), CAST(:stamp AS timestamptz))
    ON CONFLICT (slug) DO UPDATE SET
        name = EXCLUDED.name,
        work_count = EXCLUDED.work_count,
        top_ids = EXCLUDED.top_ids,
        built_at = EXCLUDED.built_at,
        content_at = {content_at_sql(TABLE)}
"""

DEFAULT = {"name": "Harry Potter", "work_count": 1000, "top": None}


def ids(length: int, seed: int = 0) -> list:
    """A top_ids value of `length` entries, so one position can move alone."""
    return [f"w{seed + i:04d}" for i in range(length)]


@pytest.fixture()
def hub_table(db):
    """conftest's session, with fandom_hubs emptied.

    conftest's `db` fixture already builds the engine against TEST_DATABASE_URL
    and applies init_db's DDL; the hub tables are in it but are not in its
    truncate list, since nothing else in the suite writes them.
    """
    db.execute(text(f"TRUNCATE {TABLE} CASCADE"))
    db.commit()
    return db


def put(db, state, stamp):
    row = {**DEFAULT, **state}
    db.execute(text(UPSERT), {
        "slug": SLUG, "name": row["name"], "wc": row["work_count"],
        "top": row["top"] or ids(40), "stamp": stamp,
    })
    db.commit()


def content_at(db):
    return db.execute(text(
        f"SELECT content_at FROM {TABLE} WHERE slug = '{SLUG}'")).scalar()


def moves(db, before, after) -> bool:
    """Does re-writing a hub from `before` to `after` move its stamp?

    The first write plants a stamp a month old, so a bump is visible as a
    difference rather than as two reads of a clock. The second write runs the
    real ON CONFLICT clause; `stamp` is only read on INSERT, so it cannot
    manufacture the answer.
    """
    put(db, before, "2026-01-01")
    was = content_at(db)
    put(db, after, "2026-01-01")
    return content_at(db) != was


class TestMaterialChange:
    """work_count moves daily for almost every hub, which is the whole problem."""

    def test_one_more_work_on_a_huge_hub_is_not_a_change(self, hub_table):
        assert not moves(hub_table, {"work_count": 52120}, {"work_count": 52121})

    def test_one_percent_on_a_huge_hub_is_a_change(self, hub_table):
        assert moves(hub_table, {"work_count": 52120}, {"work_count": 52700})

    def test_the_floor_of_ten_governs_a_small_hub(self, hub_table):
        # 1% of 90 works is under one work, so greatest(10, ...) is what applies,
        # and a hub that gains ten has visibly changed.
        assert not moves(hub_table, {"work_count": 90}, {"work_count": 99})
        assert moves(hub_table, {"work_count": 90}, {"work_count": 101})

    def test_a_drop_counts_as_much_as_a_gain(self, hub_table):
        # abs(), not a subtraction: losing works is as visible as gaining them.
        assert moves(hub_table, {"work_count": 52120}, {"work_count": 51000})

    def test_an_identical_rewrite_does_not_move_it(self, hub_table):
        # The nightly rebuild writes every hub. If this were not true the column
        # would be now() on all 12,469 of them, every night.
        assert not moves(hub_table, {}, {})


class TestLeadingEntriesOnly:
    def test_a_swap_inside_the_first_twenty_is_a_change(self, hub_table):
        assert moves(hub_table, {"top": ids(40)}, {"top": ids(40, seed=100)})

    def test_a_swap_past_the_twentieth_is_not(self, hub_table):
        # The other half of the 52%: the whole list was compared, so a reorder
        # at position 140 re-stamped a page nobody would see change.
        arr = ids(40)
        arr[30], arr[31] = arr[31], arr[30]
        assert not moves(hub_table, {"top": ids(40)}, {"top": arr})


class TestName:
    def test_a_rename_is_a_change(self, hub_table):
        # The name is the page's identity and the thing it ranks for.
        assert moves(hub_table, {}, {"name": "Harry Potter - J. K. Rowling"})


class TestEveryHubHasAStamp:
    """The sitemap's premise: no hub can be left without a <lastmod>.

    This started as a test about NULLs — the rule uses `IS DISTINCT FROM` so a
    NULL on either side reads as a change — and the schema turned out to make
    that unreachable: `name`, `work_count` and `top_ids` are all NOT NULL, and
    measured on the live table, zero of 12,469 hubs has a NULL top_ids. So
    `IS DISTINCT FROM` is defensive here rather than load-bearing, and the
    honest thing to guard is the constraint that makes it so.

    `content_at` itself being NOT NULL is the stronger half. A nullable
    content_at would let a hub into the sitemap with no lastmod at all, and a
    URL with no lastmod is one a crawler has no reason to revisit.
    """

    def test_content_at_cannot_be_null(self, hub_table):
        nullable = hub_table.execute(text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'fandom_hubs' AND column_name = 'content_at'
        """)).scalar()
        assert nullable == "NO", (
            "a nullable content_at lets a hub reach the sitemap with no lastmod")

    def test_the_columns_the_rule_compares_are_not_nullable(self, hub_table):
        # If any of these ever became nullable, `IS DISTINCT FROM` would be
        # carrying the rule rather than merely documenting it, and that change
        # should be a deliberate one.
        rows = hub_table.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'fandom_hubs'
              AND column_name IN ('name', 'work_count', 'top_ids')
              AND is_nullable = 'YES'
        """)).scalars().all()
        assert not rows, f"now nullable, so IS DISTINCT FROM is load-bearing: {rows}"

    def test_the_first_write_always_stamps(self, hub_table):
        put(hub_table, {}, "2026-01-01")
        assert content_at(hub_table) is not None