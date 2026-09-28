"""The tag bucket: 18 of 36 mismatches. WHICH tags, and why does the answer not
carry them?

  spelling     the concept is right, the answer files it differently — the
               "a concept is a GROUP of spellings" problem, one level on
  not-carried  the answer is not tagged for it at all, however apt the tag

One session up front and one at the end. The first version opened a nested
db_session inside the loop while the outer was still held and exhausted the pool
at "QueuePool limit of size 2 overflow 1" — the worker's pool is small on purpose.
"""
import sys, re, collections
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

with db_session() as db:
    rows = db.execute(sql_text("""
        SELECT p.title, coalesce(p.body,''), a.story_id, s.tags, s.title
          FROM reddit_answers a JOIN reddit_posts p ON p.id=a.post_id
          JOIN stories s ON s.id=a.story_id
         WHERE (coalesce(array_length(s.fandoms,1),0) > 0
                OR (s.summary IS NOT NULL AND s.summary <> ''))
         ORDER BY p.posted_at DESC NULLS LAST LIMIT 120
    """)).fetchall()

TAG = re.compile(r'(?<!-)tag:"([^"]*)"')
counts, cases, wanted = collections.Counter(), [], set()
pending = []
with httpx.Client(base_url="http://localhost:8000") as c:
    for title, body, sid, tags, stitle in rows:
        r = c.get("/api/search/extract", params={"text": (title+"\n"+body)[:6000]}, timeout=120)
        if r.status_code != 200: continue
        asked = TAG.findall((r.json() or {}).get("query") or "")
        if not asked: continue
        pending.append((asked, [t for t in (tags or [])], stitle or ""))
        wanted.update(a.lower() for a in asked)

sizes = {}
if wanted:
    with db_session() as db:
        for v, n in db.execute(sql_text(
            "SELECT lower(value), max(count) FROM facets WHERE kind='tag'"
            " AND lower(value) = ANY(:vs) GROUP BY 1"), {"vs": sorted(wanted)}):
            sizes[v] = n

for asked, tags, stitle in pending:
    low = {t.lower() for t in tags}
    for a in asked:
        al = a.lower()
        if al in low:
            counts["carried"] += 1; continue
        near = [t for t in tags if al in t.lower() or t.lower() in al]
        if near:
            counts["spelling"] += 1
            if len(cases) < 10: cases.append(("spelling", a, near[0], stitle[:28]))
        else:
            counts["not-carried"] += 1
            if len(cases) < 20:
                cases.append(("not-carried", f"{a} [{sizes.get(al, 0):,} works]", "", stitle[:28]))

print("=== asked tags vs what the answer carries ===")
for k, v in counts.most_common(): print(f"  {k:14} {v}")
print()
for kind, asked, near, title in cases:
    extra = f"  answer has {near[:32]!r}" if near else ""
    print(f"  [{kind:11}] {asked[:50]}{extra}   ({title})")
