"""The shared Reddit rate budget, and why recovery has to be gentle.

Everything here failed slowly in production before it was written down: a
penalty that climbed 15min -> 30 -> 60 -> 120 without the connection ever once
succeeding, because every recovery attempt was three jobs firing at the same
instant and the first one's 429 doubled the penalty for all of them.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import reddit_fetch as rf

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def _clean(db):
    db.execute(text("DELETE FROM app_settings WHERE key LIKE 'reddit%'"))
    db.commit()
    yield
    db.execute(text("DELETE FROM app_settings WHERE key LIKE 'reddit%'"))
    db.commit()


def _set(db, key, value):
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {"k": key, "v": value})
    db.commit()


def _now():
    return datetime.now(timezone.utc)


class TestTheBudgetIsShared:
    def test_a_claim_is_visible_to_the_next_claimer(self):
        """Claimed BEFORE the request, not recorded after it.

        Two processes that both read "the last request was five minutes ago"
        and then both fetch have each behaved correctly and together broken the
        limit.
        """
        first = rf._claim_slot()
        second = rf._claim_slot()
        assert first == pytest.approx(0, abs=4)
        # The second caller is made to wait out the gap the first one claimed.
        assert second >= rf.MIN_GAP - 5

    def test_an_active_backoff_refuses_everyone(self, db):
        _set(db, "reddit_backoff_until", (_now() + timedelta(hours=1)).isoformat())
        with pytest.raises(rf.Refused, match="backed off"):
            rf._claim_slot()


class TestRecoveryIsGentle:
    def test_only_one_job_probes_when_a_backoff_expires(self, db):
        """An expired backoff is permission to find out, not evidence.

        This is the ratchet, reduced: three jobs on timers all discovering the
        penalty had lifted at the same moment, all firing, and the first 429
        doubling the penalty for everyone.
        """
        _set(db, "reddit_backoff_until", (_now() - timedelta(seconds=1)).isoformat())
        _set(db, "reddit_backoff_step", "900")

        # The first caller through takes the probe and goes immediately: the
        # address has just been silent for the whole penalty.
        assert rf._claim_slot() == 0.0
        # Everyone else waits for its verdict rather than spending a request of
        # their own to learn the same thing.
        for _ in range(2):
            with pytest.raises(rf.Refused, match="probing"):
                rf._claim_slot()

    def test_a_successful_probe_clears_the_penalty_for_everyone(self, db):
        _set(db, "reddit_backoff_until", (_now() - timedelta(seconds=1)).isoformat())
        _set(db, "reddit_backoff_step", "3600")
        rf._claim_slot()
        rf._forgive()

        state = rf.budget_state()
        assert state["backed_off"] is False
        assert state["backoff_step"] == 0
        assert state["probing"] is False
        # And the budget is open again — no probe left holding it.
        rf._claim_slot()

    def test_a_failed_probe_doubles_the_penalty_and_releases_the_probe(self, db):
        _set(db, "reddit_backoff_until", (_now() - timedelta(seconds=1)).isoformat())
        _set(db, "reddit_backoff_step", "900")
        rf._claim_slot()
        rf._penalise()

        state = rf.budget_state()
        assert state["backoff_step"] == 1800
        assert state["backed_off"] is True
        # Released, so the NEXT expiry gets a fresh probe rather than finding
        # this one still held and refusing for ever.
        assert state["probing"] is False

    def test_the_penalty_is_capped(self, db):
        _set(db, "reddit_backoff_step", str(rf.BACKOFF_MAX))
        rf._penalise()
        assert rf.budget_state()["backoff_step"] == rf.BACKOFF_MAX

    def test_an_abandoned_probe_does_not_wedge_the_budget_for_ever(self, db):
        """A killed process or a restarted container must not hold it."""
        _set(db, "reddit_backoff_until", (_now() - timedelta(seconds=1)).isoformat())
        _set(db, "reddit_probe_started_at",
             (_now() - timedelta(seconds=rf.PROBE_TIMEOUT + 60)).isoformat())
        # The stale probe is ignored and this caller takes a fresh one.
        assert rf._claim_slot() == 0.0

    def test_forgiving_resets_to_zero_rather_than_halving(self, db):
        """The step records how hard we were refused LAST time. A request that
        just worked is evidence none of it still applies — halving would make
        one bad afternoon cost the rest of the day."""
        _set(db, "reddit_backoff_step", "7200")
        _set(db, "reddit_backoff_until", (_now() - timedelta(seconds=1)).isoformat())
        rf._claim_slot()
        rf._forgive()
        assert rf.budget_state()["backoff_step"] == 0
