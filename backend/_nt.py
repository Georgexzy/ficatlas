"""If tags RANKED instead of FILTERED, would the answer come back?

21 of 25 asked tags are ones the answer does not carry — and they are apt tags,
not wrong ones: Quidditch on a Quidditch fic, Time Travel on a time-travel fic.
AO3 tagging is sparse, so a conjunctive tag filter assumes something the data
does not support, and every such tag removes the right answer outright.

The decisive test is not dropping ONE term — that was measured and recovered 1
of 92 — but dropping ALL the tag terms at once, keeping fandom, ship, character
and status. If the answer comes back, tags belong in the ranking and not in the
WHERE clause.
"""
import sys, re, time
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

with db_session() as db:
    rows = db.execute(sql_text("""
        SELECT p.title, coalesce(p.body,''), a.story_id
          FROM reddit_answers a JOIN reddit_posts p ON p.id=a.post_id
          JOIN stories s ON s.id=a.story_id
         WHERE (coalesce(array_length(s.fandoms,1),0) > 0
                OR (s.summary IS NOT NULL AND s.summary <> ''))
         ORDER BY p.posted_at DESC NULLS LAST LIMIT 120
    """)).fetchall()

def rank(c, q, sid, pages=3):
    seen = 0
    for page in range(1, pages+1):
        r = c.get("/api/search", params={"q": q, "per_page": 100, "page": page}, timeout=120)
        if r.status_code != 200: return None
        res = r.json().get("results") or []
        for i, w in enumerate(res, 1):
            if str(w.get("id")) == str(sid): return seen + i
        seen += len(res)
        if len(res) < 100: break
    return None

TAGTERM = re.compile(r'(?<!-)tag:"[^"]*"\s*')
base_hits = notag_hits = both_miss = n = 0
gained = []
with httpx.Client(base_url="http://localhost:8000") as c:
    for title, body, sid in rows:
        r = c.get("/api/search/extract", params={"text": (title+"\n"+body)[:6000]}, timeout=120)
        if r.status_code != 200: continue
        q = ((r.json() or {}).get("query") or "").strip()
        if not q or 'tag:"' not in q: continue
        stripped = TAGTERM.sub("", q).strip()
        if not stripped or stripped == q: continue
        n += 1
        a = rank(c, q, sid); b = rank(c, stripped, sid)
        if a and a <= 10: base_hits += 1
        if b and b <= 10:
            notag_hits += 1
            if not (a and a <= 10): gained.append((title[:44], stripped[:60], b))
        if not a and not b: both_miss += 1
        time.sleep(0.1)

print(f"posts whose query carries a tag : {n}")
print(f"  answer in top 10 WITH tags    : {base_hits}")
print(f"  answer in top 10 WITHOUT tags : {notag_hits}")
print(f"  absent either way             : {both_miss}")
if gained:
    print("\n  recovered by dropping every tag:")
    for t, q, r_ in gained[:10]:
        print(f"    rank {r_:>3}  {t}\n              {q}")
