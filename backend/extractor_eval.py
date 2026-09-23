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

  * no query   -- the extractor answered, with nothing to search for.
  * error      -- the request itself failed. Counted apart from "no query",
                  because the first version conflated them and the count moved
                  between 17 and 10 on identical data: a flaky request looked
                  exactly like an extractor that had found nothing to say. That
                  is the same confusion that cost the FF.net enrichment its
                  queued captures and nearly cost this corpus its posts, and it
                  is worth the extra counter every time.
  * not found  -- it produced a query and the answer was not in it. These are
                  the interesting failures; --show prints them.

A pair whose answer is not in this index is skipped rather than counted as a
miss, because no query could have found it and scoring it would punish the
extractor for the archive's gaps rather than its own.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

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


class SearchFailed(Exception):
    """The search request failed. Not a statement about the query."""


# This walks the corpus as fast as it can and the site rate-limits /api/search,
# which is correct of it -- an evaluation is not entitled to more of the search
# path than a reader. Measured before this existed: nine of thirty-eight pairs
# came back HTTP 429, the count moved run to run, and the failures were being
# read as the extractor finding nothing. So requests are paced and a refusal is
# waited out rather than scored.
PACE = float(os.getenv("EVAL_PACE_SECONDS", "0.2"))
RETRIES = int(os.getenv("EVAL_RETRIES", "4"))


def _get(client, path: str, params: dict):
    """One request, waiting out a throttle instead of counting it as a miss."""
    delay = 1.0
    for attempt in range(RETRIES):
        r = client.get(path, params=params)
        if r.status_code != 429:
            return r
        time.sleep(delay)
        delay *= 2
    raise SearchFailed("HTTP 429 after retries")


def _rank_of(client, query: str, story_id, k: int,
             describe: str | None = None) -> int | None:
    """1-based rank of the right work in the results, or None if absent.

    Raises rather than returning None when the REQUEST failed, so a flaky
    search is never scored as "the extractor's query missed".
    """
    params = {"q": query, "per_page": k}
    if describe:
        params["describe"] = describe[:2000]
    r = _get(client, "/api/search", params)
    if r.status_code != 200:
        raise SearchFailed(f"HTTP {r.status_code}")
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
             "error": 0, "ranks": []}
    misses = []
    for post_id, title, body, story_id, _conf in rows:
        post = f"{title}\n{body}"[:4000]
        try:
            r = _get(client, "/api/search/extract", {"text": post})
            if r.status_code != 200:
                raise SearchFailed(f"extract HTTP {r.status_code}")
            q = (r.json().get("query") or "").strip()
        except Exception as e:
            stats["error"] += 1
            stats.setdefault("error_kinds", {})
            k_ = f"extract:{type(e).__name__}"
            stats["error_kinds"][k_] = stats["error_kinds"].get(k_, 0) + 1
            stats.setdefault("error_msg", str(e)[:200])
            continue
        if not q:
            stats["no_query"] += 1
            continue
        try:
            rank = _rank_of(client, q, story_id, k, describe=post)
        except Exception as e:
            stats["error"] += 1
            stats.setdefault("error_kinds", {})
            k_ = f"search:{type(e).__name__}"
            stats["error_kinds"][k_] = stats["error_kinds"].get(k_, 0) + 1
            stats.setdefault("error_msg", str(e)[:200])
            continue
        time.sleep(PACE)
        if rank:
            stats["found"] += 1
            stats["ranks"].append(rank)
        else:
            stats["not_found"] += 1
            if len(misses) < show:
                misses.append((title[:70], q))

    # Scored over the pairs that actually produced a verdict. Dividing by the
    # whole corpus would let a flaky run quietly improve the number by failing
    # more often.
    n = (stats["found"] + stats["not_found"] + stats["no_query"]) or 1
    stats["scored"] = n
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
    print(f"pairs        {s['pairs']}  (scored {s['scored']})")
    print(f"recall@{a.k:<6} {s['recall_at_k']}   ({s['found']} found)")
    print(f"mean rank    {s['mean_rank']}")
    print(f"no query     {s['no_query']}")
    print(f"not found    {s['not_found']}")
    if s["error"]:
        print(f"errors       {s['error']}   (requests that failed; not scored)")
        print(f"             {s.get('error_kinds')}")
        print(f"             {s.get('error_msg','')}")
    for title, q in s["misses"]:
        print(f"\n  MISS {title}\n       -> {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
