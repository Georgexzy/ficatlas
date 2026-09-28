"""Side by side: what the reader asked, what the extractor searched, what the
answer actually is. Six cases, printed in full, because the numbers now say the
query is WRONG rather than narrow and no aggregate can say how."""
import sys, time
sys.path.insert(0, "/app")
import httpx
from sqlalchemy import text as sql_text
from db.session import db_session

with db_session() as db:
    rows = db.execute(sql_text("""
        SELECT p.title, COALESCE(p.body,''), a.story_id
          FROM reddit_answers a JOIN reddit_posts p ON p.id=a.post_id
         WHERE a.story_id IS NOT NULL
         ORDER BY p.posted_at DESC NULLS LAST LIMIT 6
    """)).fetchall()

with httpx.Client(base_url="http://localhost:8000") as c:
    for title, body, sid in rows:
        r = c.get("/api/search/extract", params={"text": (title+"\n"+body)[:6000]}, timeout=120)
        q = ((r.json() or {}).get("query") or "") if r.status_code == 200 else "(extract failed)"
        with db_session() as db:
            s = db.execute(sql_text("""
                SELECT title, author, site, coalesce(kudos,0), word_count,
                       array_to_string(fandoms,' / '), array_to_string(relationships,' / '),
                       array_to_string(tags[1:14],', ')
                  FROM stories WHERE id=CAST(:i AS uuid)"""), {"i": str(sid)}).first()
        print("="*92)
        print("POST  :", title[:110])
        b = " ".join(body.split())
        print("BODY  :", (b[:330] + ("…" if len(b) > 330 else "")) if b else "(none)")
        print("QUERY :", q)
        if s:
            print(f"ANSWER: {s[0][:70]!r} by {s[1]}  [{s[2]}]  {s[3]} kudos  {s[4]} words")
            print("  fandom:", (s[5] or "")[:80])
            print("  ship  :", (s[6] or "")[:80])
            print("  tags  :", (s[7] or "")[:150])
        time.sleep(0.3)
