"""Why does each miss miss? Retrieval, or ranking?

The standing answer has been "the data": 80% of AO3 has no summary, so ranking
has nothing to rank on. That was measured months of enrichment ago, and it
decides whether extractor work is worth doing at all — so re-ask it with the
question split properly:

  excluded  the answer is not in the top 1,000 at all. Reordering cannot help;
            the query is too narrow, or a filter removed it.
  buried    the answer IS in the set, past 10. A RANKING failure, and the one
            that might be fixable without waiting a year for summaries.

And for the buried ones, what the answer row actually carries — because if the
answers have kudos and tags but no summary, the signal to rank on exists and is
simply not being used.
"""
import sys, time
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

PAGES, PER = 10, 100          # per_page is capped at 100 by the API

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

stats = {"found10":0, "buried":0, "excluded":0, "noquery":0, "err":0, "notindexed":0}
buried_ranks, buried_ids, excluded_ids = [], [], []
with httpx.Client(base_url="http://localhost:8000") as c:
    for pid, title, body, sid in rows:
        with db_session() as db:
            if not db.execute(sql_text("SELECT 1 FROM stories WHERE id=CAST(:i AS uuid)"),
                              {"i": str(sid)}).first():
                stats["notindexed"] += 1; continue
        r = get(c, "/api/search/extract", {"text": (title + "\n" + body)[:6000]})
        if r is None or r.status_code != 200: stats["err"] += 1; continue
        q = ((r.json() or {}).get("query") or "").strip()
        if not q: stats["noquery"] += 1; continue

        rank, seen = None, 0
        for page in range(1, PAGES + 1):
            rr = get(c, "/api/search", {"q": q, "per_page": PER, "page": page})
            if rr is None or rr.status_code != 200: break
            res = rr.json().get("results") or []
            for i, w in enumerate(res, 1):
                if str(w.get("id")) == str(sid): rank = seen + i; break
            seen += len(res)
            if rank or len(res) < PER: break
            time.sleep(0.1)

        if rank is None:
            stats["excluded"] += 1; excluded_ids.append(str(sid))
        elif rank <= 10:
            stats["found10"] += 1
        else:
            stats["buried"] += 1; buried_ranks.append(rank); buried_ids.append(str(sid))
        time.sleep(0.15)

print("=== why each scored post misses ===")
for k, v in stats.items(): print(f"  {k:12} {v}")

def profile(label, ids):
    if not ids: return
    with db_session() as db:
        r = db.execute(sql_text("""
            SELECT count(*), count(*) FILTER (WHERE summary IS NOT NULL AND summary<>''),
                   count(*) FILTER (WHERE kudos IS NOT NULL AND kudos>0),
                   count(*) FILTER (WHERE popularity IS NOT NULL),
                   count(*) FILTER (WHERE coalesce(array_length(tags,1),0) > 0),
                   round(avg(coalesce(kudos,0)))
              FROM stories WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": ids}).first()
    print(f"\n  {label} (n={r[0]}): summary {r[1]}  kudos {r[2]}  popularity {r[3]}"
          f"  tags {r[4]}  mean kudos {r[5]}")

if buried_ranks:
    buried_ranks.sort()
    print(f"\n  buried ranks: min {buried_ranks[0]}  median "
          f"{buried_ranks[len(buried_ranks)//2]}  max {buried_ranks[-1]}")
profile("BURIED answers", buried_ids)
profile("EXCLUDED answers", excluded_ids)
