"""The swarm alert: what makes it fire, and what must never make it fire.

The detector's threshold is not the interesting part — the interesting part is
the definition of "arrived from nowhere", because that is the clause a crawler
can write for itself. The 2026-10-20 scrape put `Referer:
https://ficatlas.com/?author=...` on 5,310 story loads, so a rule that treats
any referrer as evidence of a person would have called it an audience of
readers arriving from within the site. These tests pin the opposite: only an
EXTERNAL referrer excuses an arrival, and our own domain excuses nothing.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mailer
import scrape_watch
from api.settings import get_setting, put_setting


@pytest.fixture(autouse=True)
def _clean_alert_state(db):
    """`app_settings` is not in conftest's truncate list, and the cooldown is
    the one piece of state this module keeps. Without this, the test that
    sends an alert leaves its cooldown behind for the two that follow and they
    fail for a reason that has nothing to do with what they assert."""
    put_setting(db, "scrape_watch_last_alert", "")
    db.commit()
    yield


def _arrivals(db, n, at, ref_host=None, kind="search", path="/api/search",
              prefix="sw"):
    """n visitors, one event each — the shape a rotating address pool mints."""
    for i in range(n):
        db.execute(text("""
            INSERT INTO visit_events (at, visitor, kind, path, ref_host)
            VALUES (:at, :v, :k, :p, :r)
        """), {"at": at, "v": f"{prefix}{i:010d}", "k": kind, "p": path,
               "r": ref_host})
    db.commit()


def _owner(db, email="owner@example.test"):
    db.execute(text("""
        INSERT INTO users (username, password_hash, email, role)
        VALUES ('owner', 'x', :e, 'owner')
    """), {"e": email})
    db.commit()


def test_a_swarm_hour_is_reported_with_its_shape(db):
    """Past the threshold, and with enough detail to act on."""
    base = datetime.utcnow().replace(minute=5, second=0, microsecond=0)
    _arrivals(db, scrape_watch.WATCH_PER_HOUR + 7, base)
    hit = scrape_watch.check(db)
    assert hit is not None
    assert hit["visitors"] == scrape_watch.WATCH_PER_HOUR + 7
    assert hit["top"]["search"][0] == ("/api/search",
                                       scrape_watch.WATCH_PER_HOUR + 7)


def test_an_ordinary_hour_is_not_an_incident(db):
    """The measured organic maximum is 1-8 an hour (329 hours over 30 days,
    and the busiest honest hour was 14). Eight must stay quiet, or the mail
    becomes weather and stops being read."""
    _arrivals(db, 8, datetime.utcnow() - timedelta(minutes=20))
    assert scrape_watch.check(db) is None


def test_a_referred_crowd_is_never_a_swarm(db):
    """50 readers who arrived from Reddit, arriving together, are a good day."""
    _arrivals(db, 50, datetime.utcnow() - timedelta(minutes=20),
              ref_host="www.reddit.com", prefix="rd")
    assert scrape_watch.check(db) is None


def test_our_own_referrer_is_not_evidence_of_a_person(db):
    """The whole point. The 2026-10-20 scrape wrote our domain into its own
    Referer header, which is the one header a client writes for itself, and
    that is how it walked 3,012 story pages past the /story/ challenge.

    Any rule that lets our own domain excuse an arrival is a rule the next
    crawler already satisfies. It must fire."""
    _arrivals(db, scrape_watch.WATCH_PER_HOUR + 5,
              datetime.utcnow() - timedelta(minutes=20),
              ref_host="ficatlas.com", prefix="fr")
    assert scrape_watch.check(db) is not None


def test_the_alert_goes_out_once_per_incident(db, monkeypatch):
    """One mail for a swarm that runs for days, then a fresh one when the
    incident is over — not one per check, and not silence forever."""
    _owner(db)
    sent = []
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body, **kw:
                        sent.append((to, subject)) or True)
    _arrivals(db, scrape_watch.WATCH_PER_HOUR + 5,
              datetime.utcnow() - timedelta(minutes=5))
    now = datetime.utcnow().timestamp()

    assert scrape_watch.run(db, now=now) == "sent"
    assert len(sent) == 1 and sent[0][0] == "owner@example.test"

    assert scrape_watch.run(db, now=now + 60) == "throttled"
    assert len(sent) == 1, "the second check of the same incident is not a new mail"

    # Past the cooldown, the same still-running swarm is worth saying again.
    assert scrape_watch.run(db, now=now + scrape_watch.COOLDOWN_SEC + 60) == "sent"
    assert len(sent) == 2


def test_a_relay_that_could_not_send_is_not_six_quiet_hours(db, monkeypatch):
    """A failed delivery must not book a cooldown: the next check has to try
    again, or one bad hour of SMTP silences the alerting for the rest of the
    incident — which is the exact failure mode this module exists to end."""
    _owner(db)
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: False)
    _arrivals(db, scrape_watch.WATCH_PER_HOUR + 5,
              datetime.utcnow() - timedelta(minutes=5))
    now = datetime.utcnow().timestamp()
    assert scrape_watch.run(db, now=now) == "failed"
    assert get_setting(db, "scrape_watch_last_alert") == "", "no cooldown was booked"


def test_no_owner_address_is_silence_not_a_crash(db, monkeypatch):
    """Mail is OFF unless configured, and a detector that cannot deliver must
    not look like one that never fires — hence a distinct outcome rather than
    an exception."""
    monkeypatch.setattr(mailer, "send",
                        lambda *a, **kw: pytest.fail("must not send without a recipient"))
    _arrivals(db, scrape_watch.WATCH_PER_HOUR + 5,
              datetime.utcnow() - timedelta(minutes=5))
    assert scrape_watch.run(db) == "unconfigured"
    assert get_setting(db, "scrape_watch_last_alert") == ""


def test_a_quiet_hour_sends_nothing_and_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(mailer, "send",
                        lambda *a, **kw: pytest.fail("nothing to report"))
    _arrivals(db, 4, datetime.utcnow() - timedelta(minutes=5))
    assert scrape_watch.run(db) == "quiet"
    assert get_setting(db, "scrape_watch_last_alert") == ""
