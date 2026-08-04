#!/usr/bin/env python3
"""
Check the README's numeric claims against the live database.

    docker compose exec -T backend python /app/../tests/check-readme.py   # no
    python3 tests/check-readme.py                                          # yes

Run before pushing. The README makes concrete claims — how many works, how many
per archive, how many hosted — and those drift silently as the index grows. A
stale number in a README is not cosmetic here: it is the first thing anyone
reads to decide whether the project does what it says, and several of these
figures were wrong enough to mislead (HPFFA and DLP were listed beside AO3 and
FanFiction.net as though comparable, at 37 and 746 rows against 13.1M and 6.6M).

Deliberately tolerant. Counts grow constantly, so exact equality would fail on
every run and be ignored within a day. Each claim declares how far it may drift
before it counts as wrong.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

QUERIES = {
    "total":        "SELECT count(*) FROM stories",
    "ao3":          "SELECT count(*) FROM stories WHERE site='ao3'",
    "ffnet":        "SELECT count(*) FROM stories WHERE site='ffnet'",
    "fictionalley": "SELECT count(*) FROM stories WHERE site='fictionalley'",
    "hosted":       "SELECT count(*) FROM stories WHERE is_hosted",
    "dlp":          "SELECT count(*) FROM stories WHERE tags @> ARRAY['dlp_library']",
    "hpffa":        "SELECT count(*) FROM stories WHERE tags @> ARRAY['hpffa_archive']",
    "fandoms_ao3":  "SELECT count(*) FROM facets WHERE kind='fandom_ao3'",
}

# (label, key, regex capturing the claimed figure, scale, tolerance fraction)
CLAIMS = [
    ("AO3 works",        "ao3",          r"AO3 \(([\d.]+)M works\)",        1e6,  0.05),
    ("FanFiction.net",   "ffnet",        r"FanFiction\.net \(([\d.]+)M\)",  1e6,  0.05),
    ("FicAlley",         "fictionalley", r"FicAlley \((\d+)k\)",            1e3,  0.15),
    ("DLP curated list", "dlp",          r"recommended list \((\d+) works\)", 1,   0.25),
    ("HPFFA",            "hpffa",        r"HP FanFiction Archive \((\d+)\)", 1,    0.25),
    ("canonical fandoms","fandoms_ao3",  r"([\d,]+) canonical names",       1,    0.05),
]


def db_counts() -> dict:
    sql = " UNION ALL ".join(f"SELECT '{k}' AS k, ({q})::bigint AS v" for k, q in QUERIES.items())
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "ficatlas",
         "-d", "ficatlas", "-tAF,", "-c", sql],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        print("Could not query the database — is the stack up?")
        print(out.stderr.strip()[:300])
        sys.exit(2)
    counts = {}
    for line in out.stdout.strip().splitlines():
        if "," in line:
            k, v = line.split(",", 1)
            counts[k.strip()] = int(v)
    return counts


def main() -> int:
    text = README.read_text()
    counts = db_counts()
    bad = 0

    print(f"{'claim':22} {'README':>12} {'actual':>14}   status")
    for label, key, pattern, scale, tol in CLAIMS:
        m = re.search(pattern, text)
        actual = counts.get(key)
        if actual is None:
            continue
        if not m:
            print(f"{label:22} {'—':>12} {actual:>14,}   MISSING from README")
            bad += 1
            continue
        claimed = float(m.group(1).replace(",", "")) * scale
        drift = abs(claimed - actual) / max(actual, 1)
        ok = drift <= tol
        bad += 0 if ok else 1
        print(f"{label:22} {claimed:>12,.0f} {actual:>14,}   "
              f"{'ok' if ok else f'WRONG ({drift*100:.0f}% off)'}")

    print()
    if bad:
        print(f"{bad} claim(s) need updating in README.md")
        return 1
    print("README figures match the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
