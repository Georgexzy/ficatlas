"""Is the extractor's query too NARROW, or simply wrong?

97% of scored posts end with the answer outside the top 1,000 — not buried,
absent. That rules out ranking and leaves two possibilities, which need
different fixes:

  over-constrained  every term is defensible but together they exclude the
                    answer. Dropping one brings it back. Fix: choose fewer or
                    different terms.
  wrong             no subset finds it. The extractor read the post wrongly.
                    Fix: read the post better — which is where labelled data
                    would earn its place.

So: run the built query, then run it again with each term removed in turn, and
see which (if any) recovers the answer. This is the ablation CLAUDE.md already
ran across 24 posts to find what blocks works — asked here per POST, against a
known right answer.
"""
import sys, time, re, json
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

PER = 100
DEPTH = 3            # pages to scan = 300 results

with db_session() as db:
    rows = db.execute(sql_text("""
        SELECT p.id, p.title, COALESCE(p.body,''), a.story_id
          FROM reddit_answers a JOIN reddit_posts p ON p.id=a.post_id
         WHERE a.story_id IS NOT NULL
         ORDER BY p.posted_at DESC NULLS LAST LIMIT 200
    """)).fetchall()

def get(c, path, params, tries=5):
    d = 1.0
    for _ in range(tries):
        r = c.get(path, params=params, timeout=120)
        if r.status_code != 429: return r
        time.sleep(d); d *= 2
    return None

def rank(c, q, sid, depth=DEPTH):
    seen = 0
    for page in range(1, depth + 1):
        r = get(c, "/api/search", {"q": q, "per_page": PER, "page": page})
        if r is None or r.status_code != 200: return None, seen
        res = r.json().get("results") or []
        for i, w in enumerate(res, 1):
            if str(w.get("id")) == str(sid): return seen + i, seen + len(res)
        seen += len(res)
        if len(res) < PER: break
    return None, seen

# Split a bar query into its terms without needing the parser: operators are
# key:"value" or bare words, and only the operator terms are droppable.
TERM = re.compile(r'(-?\w+:"[^"]*"|-?\w+:\S+|\S+)')

recovered, still_gone, already_ok, n = 0, 0, 0, 0
by_term = {}
with httpx.Client(base_url="http://localhost:8000") as c:
    for pid, title, body, sid in rows:
        r = get(c, "/api/search/extract", {"text": (title + "\n" + body)[:6000]})
        if r is None or r.status_code != 200: continue
        q = ((r.json() or {}).get("query") or "").strip()
        if not q: continue
        n += 1
        rk, _ = rank(c, q, sid)
        if rk and rk <= 10: already_ok += 1; continue
        terms = TERM.findall(q)
        won = None
        for t in terms:
            if ":" not in t: continue          # plain text, not a droppable term
            sub = " ".join(x for x in terms if x != t).strip()
            if not sub: continue
            rk2, _ = rank(c, sub, sid, depth=1)
            if rk2 and rk2 <= 10:
                won = t; break
            time.sleep(0.1)
        if won:
            recovered += 1
            key = won.split(":")[0]
            by_term[key] = by_term.get(key, 0) + 1
        else:
            still_gone += 1
        time.sleep(0.15)

print(f"=== {n} posts with a query ===")
print(f"  already in top 10                 {already_ok}")
print(f"  RECOVERED by dropping ONE term    {recovered}")
print(f"  still absent after every 1-drop    {still_gone}")
if by_term:
    print("\n  which single term was blocking it:")
    for k, v in sorted(by_term.items(), key=lambda kv: -kv[1]):
        print(f"    {k:14} {v}")
