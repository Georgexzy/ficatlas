"""Does the extractor find the fic the reader was actually describing?

Every change to the extractor until now was argued from examples read by eye,
because the question it exists to answer -- would this query have found the
right fic -- had no number attached to it. This attaches one.

    python extractor_eval.py                 # all pairs
    python extractor_eval.py --confirmed     # only the ones the OP agreed with
    python extractor_eval.py --sample 50 --k 10

The corpus is reddit_answers: lost-fic posts paired with the work a commenter
linked, built slowly by _reddit_answers_loop. See reddit_answers.py for why a
linked work counts as an answer without a "solved" flair, and why `confirmed`
-- the original poster linking it back -- is the stronger label.

What is reported
----------------
recall@k, the share of posts whose real answer appears in the first k results
of the query the extractor produced. Recall rather than precision because a
fic-finder post has exactly one right answer and the reader will happily scan
ten; a query that puts it at rank 8 has done its job, and one that returns it
nowhere has not, however tidy the terms look.

Also reported, and worth as much:

  * no query   -- the extractor produced nothing at all to search with.
  * not found  -- it produced a query and the answer was not in it. These are
                  the interesting failures; --show prints them.

A pair whose answer is not in this index is skipped rather than counted as a
miss, because no query could have found it and scoring it would punish the
extractor for the archive's gaps rather than its own.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")
from sqlalchemy import text as sql_text  # noqa: E402

from db.session import db_session  # noqa: E402

CORPUS_SQL = """
    SELECT p.id, p.title, COALESCE(p.body, ''), a.story_id, a.confirmed
      FROM reddit_answers a
      JOIN reddit_posts p ON p.id = a.post_id
     WHERE a.story_id IS NOT NULL
       AND (:confirmed_only = FALSE OR a.confirmed)
     ORDER BY p.posted_at DESC NULLS LAST
     LIMIT :lim
"""


def _rank_of(client, query: str, story_id, k: int) -> int | None:
    """1-based rank of the right work in the results, or None."""
    r = client.get("/api/search", params={"q": query, "per_page": k})
    if r.status_code != 200:
        return None
    for i, w in enumerate(r.json().get("results") or [], 1):
        if str(w.get("id")) == str(story_id):
            return i
    return None


def run(k: int = 10, sample: int | None = None, confirmed_only: bool = False,
        show: int = 0) -> dict:
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    with db_session() as db:
        rows = db.execute(sql_text(CORPUS_SQL),
                          {"confirmed_only": confirmed_only,
                           "lim": sample or 100000}).fetchall()

    stats = {"pairs": len(rows), "no_query": 0, "found": 0, "not_found": 0,
             "ranks": []}
    misses = []
    for post_id, title, body, story_id, _conf in rows:
        post = f"{title}\n{body}"[:4000]
        q = (client.get("/api/search/extract", params={"text": post})
             .json().get("query") or "").strip()
        if not q:
            stats["no_query"] += 1
            continue
        rank = _rank_of(client, q, story_id, k)
        if rank:
            stats["found"] += 1
            stats["ranks"].append(rank)
        else:
            stats["not_found"] += 1
            if len(misses) < show:
                misses.append((title[:70], q))

    n = stats["pairs"] or 1
    stats["recall_at_k"] = round(stats["found"] / n, 3)
    stats["mean_rank"] = (round(sum(stats["ranks"]) / len(stats["ranks"]), 2)
                          if stats["ranks"] else None)
    stats["misses"] = misses
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--confirmed", action="store_true",
                    help="only pairs the original poster agreed with")
    ap.add_argument("--show", type=int, default=0,
                    help="print this many failing posts and their queries")
    a = ap.parse_args()

    s = run(k=a.k, sample=a.sample, confirmed_only=a.confirmed, show=a.show)
    if not s["pairs"]:
        print("No pairs yet — the corpus is still being harvested "
              "(see _reddit_answers_loop).")
        return 1
    print(f"pairs        {s['pairs']}")
    print(f"recall@{a.k:<6} {s['recall_at_k']}   ({s['found']} found)")
    print(f"mean rank    {s['mean_rank']}")
    print(f"no query     {s['no_query']}")
    print(f"not found    {s['not_found']}")
    for title, q in s["misses"]:
        print(f"\n  MISS {title}\n       -> {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
