"""Which PART of the built query excludes the known answer?

90 of 93 answers sit outside the top 1,000 and dropping any single term recovers
only one, so the queries are wrong rather than narrow. "Wrong" is not one bug
though, and this says which: for each post, take the query the extractor built,
and check each term against the ANSWER's own row. A term the answer does not
carry is a term that excluded it.

Mechanical, so it scales past the six posts anybody would read by hand — and it
names the FIELD, which is what decides where to look next.
"""
import sys, re, collections
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

with db_session() as db:
    rows = db.execute(sql_text("""
        SELECT p.title, coalesce(p.body,''), a.story_id
          FROM reddit_answers a JOIN reddit_posts p ON p.id=a.post_id
          JOIN stories s ON s.id=a.story_id
         WHERE a.story_id IS NOT NULL
           AND (coalesce(array_length(s.fandoms,1),0) > 0
                OR (s.summary IS NOT NULL AND s.summary <> ''))
         ORDER BY p.posted_at DESC NULLS LAST LIMIT 120
    """)).fetchall()

TERM = re.compile(r'(-?)(\w+):"([^"]*)"|(-?)(\w+):(\S+)')
blame = collections.Counter()
examples = collections.defaultdict(list)

def norm(x): return (x or "").strip().lower()

with httpx.Client(base_url="http://localhost:8000") as c:
    for title, body, sid in rows:
        r = c.get("/api/search/extract", params={"text": (title+"\n"+body)[:6000]}, timeout=120)
        if r.status_code != 200: continue
        q = ((r.json() or {}).get("query") or "").strip()
        if not q: continue
        with db_session() as db:
            s = db.execute(sql_text("""
                SELECT fandoms, relationships, characters, tags, status::text,
                       word_count, title FROM stories WHERE id=CAST(:i AS uuid)"""),
                {"i": str(sid)}).first()
        if not s: continue
        fandoms  = {norm(x) for x in (s[0] or [])}
        ships    = {norm(x) for x in (s[1] or [])}
        chars    = {norm(x) for x in (s[2] or [])}
        tags     = {norm(x) for x in (s[3] or [])}
        status   = norm(s[4]); words = s[5] or 0

        for m in TERM.finditer(q):
            neg = m.group(1) or m.group(4)
            key = (m.group(2) or m.group(5) or "").lower()
            val = norm(m.group(3) if m.group(3) is not None else m.group(6))
            if neg: continue                     # exclusions measured as harmless
            hit = None
            if key in ("fandom",):        hit = any(val in f or f in val for f in fandoms)
            elif key in ("ship","relationship"): hit = any(val in r_ or r_ in val for r_ in ships)
            elif key in ("char","character"):    hit = any(val in ch or ch in val for ch in chars)
            elif key == "tag":           hit = any(val in t or t in val for t in tags)
            elif key == "words":         continue
            if hit is False:
                blame[key] += 1
                if len(examples[key]) < 3:
                    examples[key].append((val[:38], (s[6] or "")[:34]))
        # bare status words
        for w in ("complete", "wip"):
            if re.search(rf"\b{w}\b", q):
                want = "complete" if w == "complete" else "in_progress"
                if status and status != want:
                    blame["status"] += 1
                    if len(examples["status"]) < 3:
                        examples["status"].append((w, f"answer is {status}"))

print("=== which part of the query excluded the right answer ===")
for k, v in blame.most_common():
    print(f"  {k:12} {v}")
    for a, b in examples[k]:
        print(f"      asked {a!r} — answer: {b}")
