"""How often does the status fix change the query on REAL posts, and which way?

My own test sentences are not evidence — I wrote them to fail. This runs the old
rule and the new one over every post in the corpus and reports the disagreements,
so the change is measured against what readers actually wrote.
"""
import sys, re
sys.path.insert(0, "/app")
from sqlalchemy import text as sql_text
from db.session import db_session
from api.search import _STATUS_WORDS, _STATUS_REFUSAL, _read_status

def old_rule(raw):
    for rx, value in _STATUS_WORDS:
        if rx.search(raw or ""):
            return value
    return None

with db_session() as db:
    rows = db.execute(sql_text(
        "SELECT title, coalesce(body,'') FROM reddit_posts ORDER BY posted_at DESC LIMIT 420"
    )).fetchall()

same = 0
changed = {}
examples = []
for title, body in rows:
    raw = f"{title}\n{body}"
    o, n = old_rule(raw), _read_status(raw)
    if o == n: same += 1; continue
    key = f"{o} -> {n}"
    changed[key] = changed.get(key, 0) + 1
    if len(examples) < 6:
        m = re.search(r"[^.!?\n]{0,80}(?:unfinished|wip|ongoing|complet\w+|finished)[^.!?\n]{0,50}",
                      raw, re.I)
        examples.append((key, (m.group(0) if m else title)[:120]))

print(f"posts examined : {len(rows)}")
print(f"unchanged      : {same}")
print(f"changed        : {len(rows)-same}")
for k, v in sorted(changed.items(), key=lambda kv: -kv[1]):
    print(f"   {k:24} {v}")
print("\nwhat the changed posts actually say:")
for k, ex in examples:
    print(f"   [{k}] …{ex}…")
