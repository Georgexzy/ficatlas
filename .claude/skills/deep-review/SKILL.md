---
name: deep-review
description: >-
  A deep, evidence-led review of FicAtlas: not only "is this diff correct" but
  "what does this change make the running system do, and what does it make it
  stop doing". Reviews the changed code, the subsystem around it, the standing
  invariants of the index, and the operational surface — verifying every claim
  against the live database, the running API and the test suites rather than
  reasoning from the source alone. Use for the review loop, before a promote, or
  whenever a change touches the index, the worker loops, the search path, the
  public tier, or anything that mutates data irreversibly.
---

# Deep review

The ordinary review answers "is this diff correct?". That question has been
answered well here and has still let three classes of fault through, each of
which reached production:

* **Second-order faults.** A guard was added to stop a network blip renumbering
  an author's series. It was correct, and it left the fill loop unable to
  complete anything, so it burned AO3 requests every fifteen minutes achieving
  nothing. Nothing was wrong with the diff. Something was wrong with what the
  system did afterwards.
* **Frame faults.** A credential was removed from eighteen files and replaced
  with a DSN composed from `POSTGRES_*` — correct-looking, and broken inside the
  stack, where no process has `POSTGRES_*` at all. The diff was fine. The
  environment it runs in was not what the diff assumed.
* **Confidently wrong findings.** A review argued that a ranking bonus could not
  reach broad queries. The reasoning was sound and the conclusion was false —
  all twenty results on page one carried the pairing. Another argued a date
  helper used the wrong timezone frame; acting on it *introduced* the bug it
  claimed to fix, because the data is UTC-bucketed and the original was right.

So this review is built around one rule.

## The rule: establish, don't argue

**A finding is a claim about the running system, and you have the running system.**
Do not submit reasoning where evidence is available. For every finding, do the
cheapest thing that would settle it:

| To claim | Establish it by |
|---|---|
| a query is wrong / slow | run it: `docker exec ficatlas-db-1 psql -U ficatlas -d ficatlas -c "…"`, with `EXPLAIN (ANALYZE)` for cost |
| an endpoint misbehaves | call it: `docker exec ficatlas-backend-1 python -c "import urllib.request…"`, or `curl https://ficatlas.com/api/…` for the public tier |
| a function returns the wrong thing | import it in the container and call it with the failing input |
| a change affects N rows/pages | `SELECT count(*)` with the actual predicate — never estimate |
| an env var is set | `docker inspect <container>` or `docker exec <container> env` |
| a config is live | `docker exec <c> md5sum <file>` against the host copy — the nginx conf is bind-mounted and the container can serve stale content |
| something is unused | `git ls-files -z \| xargs -0 grep -n` across the tree, then check the DB for a table/column of that name |
| a test would catch it | write the failing case and run it |

Label every finding with how it was established:

* **MEASURED** — you ran it and observed the fault. State the numbers.
* **REPRODUCED** — you constructed the input and saw the wrong output.
* **REASONED** — you could not run it. Say why, and say what would settle it.

A REASONED finding on a system this reachable is a request for someone else to
do your work. Keep them rare, and never rank one above a MEASURED finding.

**Report what you disproved.** A finding you investigated and rejected is worth
as much as one you confirmed, because it stops the next reader re-raising it and
stops the author "fixing" something that was right. Give the evidence.

## Scope, in four rings

Work outward. Ring 1 alone is the ordinary review; a diff-empty tree means rings
2–4 are the whole job, not that there is nothing to do.

**Ring 1 — the change.** `git diff @{upstream}...HEAD`, plus the working tree.
Correctness, and the repo's own rules in `CLAUDE.md`. That file is a list of
faults that already happened; a change that re-creates one is the highest-value
find in the review.

**Ring 2 — the subsystem.** Whatever the change touches, read the whole of it,
not the changed lines. The bug is frequently in the unchanged code that the
change has now made reachable, or in the caller that passes a default the new
parameter did not anticipate.

**Ring 3 — consequences.** For each change, ask what *stops* happening:

* Which **worker loop** consumes this, and can it now be starved, stalled, or
  made to spin? (`worker.py` runs ~18 loops; check the one that calls this.)
* Does it run **blocking DB work on the event loop**? Every loop but one uses
  `asyncio.to_thread`; the exception stalled every crawler in the worker.
* Does a **queue or ranking** become deterministic in a way that re-selects the
  same rows forever? That is the starvation class, and it has bitten twice.
* Does it change what a **cache** holds? `search_cache.SCHEMA_VERSION` must be
  bumped when the response changes; in-process caches survive until restart.
* Does it widen or narrow a **search predicate**? Narrowing silently deletes
  results — `popularity_desc` once hid 97% of matches.
* Is any **data mutation irreversible**? Renumbering, deleting, overwriting a
  field the archive will not give back. Those need a guard and a test, and they
  outrank everything else in this document.

**Ring 4 — the operational surface.** Regardless of the diff:

* **The public tier is a separate build.** Code merged to `main` is not live
  until `deploy/promote.sh`. Check what is actually serving:
  `./deploy/promote.sh --status`, and `docker exec <web> grep -o 'http://[a-z0-9:.-]*' .next/routes-manifest.json` — Next config is baked at build time, so a runtime env var that looks right proves nothing.
* **The database is shared with the live site** and holds ~20M rows. A migration
  or index added to `init_db.py` is built non-concurrently at startup.
* **Credentials**: `python3 tests/check-secrets.py` must pass, and the hook must
  be installed (`git config core.hooksPath .githooks`).
* **Backups**: anything that changes what "hosted" means must change
  `backup.sh essential`'s predicate in the same commit.
* **Documentation drift**: `python3 tests/check-readme.py`. If the change
  encodes a lesson that cost real time, it belongs in `CLAUDE.md` Gotchas.

## What counts as a finding

Rank by **blast radius, measured**: how many rows, pages, or requests are wrong,
and whether the damage is recoverable. "Wrong sentence on 10,159 series pages"
and "irreversibly renumbers an author's series on a network blip" are the shape
of a real finding. Ordering:

1. **Irreversible data loss or corruption.** Nothing else competes.
2. **Silently wrong output at scale** — the site asserting something false.
3. **Live faults**: 500s, stalls, starvation, unbounded growth, a security hole.
4. **Faults under load or over time** that are fine today: an unbounded query
   under a 60s statement timeout, a cache that never expires, a pool exhausted
   at `WEB_CONCURRENCY=4`.
5. **Missing invariant tests** for any of the above — specifically where a test
   would have caught a fault this repo has already had.
6. **Reuse and simplification**, where the duplication is real and load-bearing.

## Not findings

Style, formatting, naming, import order. Anything a typechecker or linter
catches. Pre-existing issues untouched by the change, unless they are in ring 4
and genuinely dangerous. Missing tests in general — only where an invariant that
has already broken is unguarded. Deliberate decisions documented in a comment or
in `CLAUDE.md`: this codebase argues with itself in prose, and a comment saying
"this looks wrong and is not, because…" is an answer, not a target. Read it
before flagging what it defends.

## Fix what you find

A review that only lists is a review that gets read once and re-run next week
against the same faults. **Fix them in the same pass**, in this order, and stop
at the first line you cannot cross:

1. **Fix, with a test**, anything in classes 1–4 (data loss, wrong output at
   scale, live faults, faults under load) where the fix is contained and the
   correct behaviour is not a judgement call. Add the regression test first,
   watch it fail, then fix it.
2. **Fix and flag** where the fix is contained but the *right answer* is
   arguable. Make the conservative choice, say plainly in the report that you
   chose it, and say what the alternative was.
3. **Do not fix, report instead**, when any of these is true:
   * the correct behaviour is a product decision (what a page should SAY, what
     a threshold should BE, which of two defensible orderings is wanted);
   * the change is to work someone else is visibly mid-way through;
   * the fix is large enough to need its own design;
   * you cannot construct a test that would have caught it.
   Say which of these applies. "Not fixed" with a reason is a finding; "not
   fixed" in silence is an omission.

Rules for fixing:

* **Verify the fault before you fix it.** A finding you did not reproduce is a
  finding you may be about to "fix" into a real bug. This has happened here: a
  date helper was changed on a well-argued but false report and the change
  introduced the bug the report described.
* **Re-run the whole affected surface afterwards**, not just the new test:
  `docker exec ficatlas-backend-1 python -m pytest tests/ -q` and
  `docker compose run --rm --no-deps -T frontend npx vitest run`.
* **One commit per fault**, with the message explaining what was wrong and how
  it was established. Never chain `commit && promote` — a rejected commit hook
  will otherwise deploy an uncommitted tree.
* **Do not deploy as part of the review.** Leave that to a human, and say what
  is waiting.

## Sequential coverage

The site is bigger than one pass. Reviewing "the diff" forever means the
untouched 90% is never looked at, and that is where the oldest faults are.

`.claude/skills/deep-review/COVERAGE.md` holds the areas and when each was last
reviewed. On each run:

1. Read it. Pick the area with the **oldest** review date, unless the current
   diff touches an area — then take that one, since it is both changed and due.
2. Review that area to the full depth above. Rings 1–3 apply to the area rather
   than to a diff: read all of it, test it against the live system, and ask what
   it makes the rest of the system do.
3. Ring 4 is checked **every** run regardless of area — it is cheap and it is
   what tells you the site is actually up and serving what you think.
4. Update the area's row: the date, what you fixed, what you left and why.

An area is "covered" when you have read all of its code, exercised its main
paths against the live system, and either fixed or consciously accepted every
fault you found. Covering one area properly beats skimming four.

## Output

Findings first, most severe first. For each:

```
<file>:<line> — <SEVERITY> — <one sentence: the defect, not the fix>
<how it was established: MEASURED / REPRODUCED / REASONED, with the evidence>
<the failure: concrete inputs or state -> wrong output, and how many are affected>
<what would fix it — one line, no patch>
```

Then **Checked and sound**: what you verified and found correct, with the
evidence — including any earlier finding you now believe was wrong.

Then, only if you are confident: **Not looked at**, naming what the review did
not cover, so nobody reads silence as a clean bill.

Use `ReportFindings` if it is available. If it is not, print the findings as
text and say so — do not skip the review.
