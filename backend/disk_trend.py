"""How fast the disk is filling, and when it fills, from samples taken over time.

The question "when does the box fill up" was answered once by hand from two
dated figures someone had happened to write down (2026-09-03: database 39GB,
disk 71%; 2026-10-06: 49GB, 81%). That is a rate from two points a month apart,
and nothing kept measuring. The worker now records one sample every six hours
into `app_settings`, and the admin panel reads the rate off the samples.

What is sampled, and why both:
  - the WHOLE filesystem the repo lives on (statvfs of /app, which is the
    host's root filesystem), because that is what actually runs out -- the
    database shares it with Docker images, backups and everything else on the
    box;
  - the database's own size, because that is the part FicAtlas controls and
    the part a crawl or a backfill moves.

The rate is taken over the last ~30 days rather than end to end: a one-off
(a backfill, a cleanup that freed 17GB) should age out of the projection
rather than bend it for ever.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

log = logging.getLogger("disk_trend")

KEY = "disk_samples"
MAX_SAMPLES = 4 * 400           # six-hourly for over a year
PATH = os.getenv("DISK_TREND_PATH", "/app")
WINDOW_DAYS = 30


def _load(db) -> list:
    try:
        raw = db.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                         {"k": KEY}).scalar()
        s = json.loads(raw) if raw else []
        return s if isinstance(s, list) else []
    except Exception:
        return []


def sample(db) -> list:
    """Append (time, db bytes, used bytes, total bytes) and store it."""
    st = os.statvfs(PATH)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    dbb = int(db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
    samples = _load(db)
    samples.append([datetime.now(timezone.utc).isoformat(), dbb, total - free, total])
    samples = samples[-MAX_SAMPLES:]
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {"k": KEY, "v": json.dumps(samples)})
    db.commit()
    return samples


def trend(samples: list, now: datetime | None = None) -> dict | None:
    """Rates and projections from the samples, or None with fewer than one.

    With a single sample there is a size and no rate. A rate needs two
    samples at least a day apart: six hours of growth multiplied up to a
    month is noise wearing a number's clothes.
    """
    if not samples:
        return None
    now = now or datetime.now(timezone.utc)
    last = samples[-1]
    t1 = datetime.fromisoformat(last[0])
    out = {"db_bytes": last[1], "used_bytes": last[2], "total_bytes": last[3],
           "free_bytes": last[3] - last[2], "samples": len(samples),
           "used_per_day": None, "db_per_day": None, "window_days": None,
           "full_in_days": None, "ninety_in_days": None}
    since = t1 - timedelta(days=WINDOW_DAYS)
    # The newest sample at least WINDOW_DAYS old, else the oldest there is.
    older = [s for s in samples if datetime.fromisoformat(s[0]) <= since]
    first = older[-1] if older else samples[0]
    t0 = datetime.fromisoformat(first[0])
    days = (t1 - t0).total_seconds() / 86400
    if days < 1:
        return out
    used_rate = (last[2] - first[2]) / days
    out.update(used_per_day=used_rate, db_per_day=(last[1] - first[1]) / days,
               window_days=round(days, 1))
    if used_rate > 0:
        out["full_in_days"] = round(out["free_bytes"] / used_rate)
        to_ninety = 0.9 * last[3] - last[2]
        out["ninety_in_days"] = max(0, round(to_ninety / used_rate))
    return out
