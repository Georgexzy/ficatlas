r"""Teach the deterministic extractor, then go away.

    docker compose exec backend python extractor_teacher.py --limit 20 --dry-run
    docker compose exec backend python extractor_teacher.py --limit 200

What this is, and what it is not
--------------------------------
A model reads fic-finder posts OFFLINE and writes down what each one is
actually asking for. Nothing here is ever called while a reader is searching:
the output is a table of labels, and those labels become vocabulary, rules and
test cases inside the deterministic extractor -- exactly as tag_hints mines
phrase-to-tag mappings out of summaries today.

That boundary is the whole design. When the teaching is done this script stops
being run and NOTHING CHANGES, because no request path touches it. If switching
it off altered behaviour, that would be the bug.

Why it is needed
----------------
The extractor is measured against reddit_answers -- posts paired with the fic
somebody named in the comments -- and that corpus is thin and narrow. It has 93
usable pairs, not one of which contains a fic link, and only eight carry a
negation. It can tell us the answer was missed; it cannot tell us WHICH PART of
the post the extractor failed to understand, and it says nothing at all about
posts nobody ever answered.

A label says what the post asked for, independently of whether anyone replied.

Proposals, not truth
--------------------
Everything the model returns is checked against this index before it is kept.
A tag it invents is discarded; a fandom nobody uses is discarded. It is good at
reading a paragraph and saying "this person wants a slow burn with a happy
ending, and does not want major character death" -- and it has no idea what
this archive happens to call those things. So it proposes and the vocabulary
disposes, which is also what keeps a hallucination out of the extractor.

The phrase is recorded beside every want
----------------------------------------
Each want carries the exact words from the post that justify it. That pairing
is the point: "they pretend to be dating" -> Fake/Pretend Relationship is a
phrase-to-tag mapping of precisely the kind tag_hints could not mine, because
mining bigrams statistically needs a sample this machine cannot hold (see
tag_hints.mine_bigrams, and the 3% run that was OOM-killed).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

import httpx  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from db.session import db_session  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("teacher")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.getenv("TEACHER_MODEL", "claude-haiku-4-5-20251001")
# Between calls. Not a rate limit so much as a reminder that this is a
# background chore competing with a live site for the same box.
GAP_SECONDS = float(os.getenv("TEACHER_GAP_SECONDS", "1.0"))
MAX_TOKENS = 1200

DDL = """
CREATE TABLE IF NOT EXISTS post_labels (
    post_id     TEXT PRIMARY KEY,
    -- What the model read out of the post, before any checking.
    raw         JSONB NOT NULL,
    -- The same, reduced to things this index actually has. See `_ground`.
    grounded    JSONB NOT NULL,
    model       TEXT NOT NULL,
    labelled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SYSTEM = """You read fanfiction "help me find a fic" posts and say what the \
reader is asking for.

Answer with JSON only, no prose, in exactly this shape:

{
  "fandom": "the single fandom, as the reader would say it, or null",
  "pairing": "Name/Name if they named a romantic pairing, else null",
  "characters": ["characters named as important"],
  "wants": [{"phrase": "the exact words from the post", "idea": "a short \
canonical name for what those words describe"}],
  "excludes": [{"phrase": "the exact words", "idea": "short canonical name"}],
  "length": "long" | "short" | null,
  "status": "complete" | "ongoing" | null,
  "is_lost_fic": true if they are trying to re-find one specific fic they \
already read, false if they want recommendations
}

Rules:
- "phrase" must be copied verbatim from the post. It is what justifies the \
entry, and an entry without one is worthless.
- "idea" must be a SHORT tag in the register an archive actually uses. Real
  examples, to show the style and length: Fluff, Angst, Hurt/Comfort, Slow
  Burn, Friends to Lovers, Enemies to Lovers, Fake/Pretend Relationship,
  Alternate Universe, Time Travel, Amnesia, Mutual Pining, Happy Ending, Major
  Character Death, Canon Divergence, Soulmates, Alpha/Beta/Omega Dynamics,
  Whump, Angst with a Happy Ending, Found Family, Redemption.
  Two or three words. NOT a description of the plot, NOT a composite joined
  with a slash or "and", NOT a phrase you invented to fit this one post.
  "Bureaucratic Fantasy Worldbuilding" is wrong; "Worldbuilding" is right.
  "Character Parallel / Foil" is wrong; leave it out.
- If no short archive-style tag fits, OMIT the entry. A want that has to be
  described in a sentence is one the archive has no name for, and guessing at
  one is worse than saying nothing: it is checked against the real vocabulary
  afterwards and a near-miss resolves to something unrelated.
- Only include a want the reader actually expressed. Do not infer what is \
typical of the fandom.
- An exclusion is anything they ruled out: "no smut", "not a coffee shop AU", \
"please no major character death".
- Leave a field null rather than guessing."""


class TeacherRefused(Exception):
    """The API declined, with the reason it gave.

    Separate from a parse failure or a network blip because the useful ones are
    account-level and identical on every post -- no credit, a revoked key, a
    model name that does not exist. Those should stop the run and say so, not
    scroll past a hundred times.
    """


def _post_text(title: str, body: str, cap: int = 3500) -> str:
    return f"{title}\n\n{body or ''}"[:cap]


def label_post(client: httpx.Client, api_key: str, title: str, body: str) -> dict:
    """One post, read by the model. Raises on anything that is not a label."""
    r = client.post(
        API_URL,
        headers={"x-api-key": api_key,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM,
              "messages": [{"role": "user", "content": _post_text(title, body)}]},
        timeout=90,
    )
    if r.status_code >= 400:
        # Say what is actually wrong. A bare HTTPStatusError on every post
        # reads as "the teacher is broken", and the first real run said exactly
        # that for four posts in a row when the true answer was one sentence
        # from the API: the key was valid and the account had no credit.
        try:
            detail = r.json().get("error", {}).get("message", "")
        except Exception:
            detail = r.text[:200]
        raise TeacherRefused(f"HTTP {r.status_code}: {detail}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    # The model is asked for bare JSON and usually obliges; a fenced block is
    # the one deviation worth tolerating rather than discarding the call.
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    return json.loads(text)


# How close a proposed name has to be to something this index really has.
# Trigram similarity, the same measure the facet suggester uses.
# Declared and, for one run, never applied -- which is how "Character Parallel
# / Foil" became `Pansexual Character` and "Loss of Agency" became `Loss of
# Limbs`. Without a floor the query takes the best of whatever pg_trgm's 0.3
# default lets through, and on a composite name the best match is often a
# single shared word. A grounding that always finds SOMETHING is worse than one
# that finds nothing: it launders an invention into the vocabulary.
_MIN_SIM = float(os.getenv("TEACHER_MIN_SIM", "0.62"))
# And how many works must carry it. A tag on four works is not a trope the
# extractor should learn, whatever the model called it.
_MIN_WORKS = int(os.getenv("TEACHER_MIN_WORKS", "200"))


def _resolve(db, kind: str, name: str):
    """The closest thing this index actually has to `name`, or None.

    The model is good at reading a paragraph and bad at knowing what this
    archive calls things -- it will happily answer "Slow Burn Romance" where
    the vocabulary says `Slow Burn`, or invent a tag that reads perfectly and
    exists nowhere. So every proposal is resolved here, and an unresolvable one
    is dropped rather than stored.

    This is also the hallucination guard. Nothing the model makes up can reach
    the extractor, because the extractor only ever learns names that were
    already in the vocabulary.
    """
    if not name or len(name) < 3:
        return None
    try:
        row = db.execute(sql_text("""
            SELECT value, count FROM facets
             WHERE kind = :k AND count >= :min_works
               -- A single %, not %%. SQLAlchemy's text() does no
               -- %-formatting, so a doubled one reaches Postgres verbatim and
               -- fails with "operator does not exist: text %% unknown" --
               -- which _resolve then swallowed, so every proposal resolved to
               -- nothing and every want was silently dropped.
               AND (lower(value) = lower(:n)
                    OR (value % :n AND similarity(value, :n) >= :min_sim))
             ORDER BY (lower(value) = lower(:n)) DESC,
                      similarity(value, :n) DESC,
                      count DESC
             LIMIT 1
        """), {"k": kind, "n": name, "min_works": _MIN_WORKS,
               "min_sim": _MIN_SIM}).first()
    except Exception:
        log.debug("resolve failed for %r", name, exc_info=True)
        return None
    return {"value": row[0], "count": row[1]} if row else None


def _ground(db, raw: dict) -> dict:
    """Keep only what this index can act on, and say what was dropped.

    `dropped` is recorded deliberately. A want the model read correctly and
    this archive has no name for is not noise -- it is a gap in the vocabulary,
    and a list of them is the most direct evidence available of what readers
    ask for that we cannot search.
    """
    out: dict = {"wants": [], "excludes": [], "dropped": []}

    # FANDOM and PAIRING go through the extractor's own resolvers first.
    #
    # Readers write nicknames and so does a model reading them: this one
    # answered "Steve/Bucky", which is not what the archive files the pairing
    # under. Raw trigram matched it only because the floor was missing, and
    # once the floor went in it stopped matching at all -- the right answer is
    # not a looser floor but the machinery that already solves this, the same
    # one that turns "Harry/Daphne" into `Daphne Greengrass/Harry Potter`.
    name = raw.get("pairing")
    if name:
        # DIRECT match first, the pair resolver only as a fallback.
        #
        # Tried the other way round it turned "Harry Potter/Voldemort" into
        # `Delphi & Voldemort (Harry Potter)`: the resolver takes each half as
        # a character and finds a pairing containing them, which is exactly
        # what rescues a nickname like "Steve/Bucky" and exactly what goes
        # wrong when the model already wrote something close to the archive's
        # own name. So the near-exact name wins when there is one.
        hit = _resolve(db, "relationship", name)
        if not hit:
            try:
                from api.search import _resolve_pair
                resolved = _resolve_pair(db, name)
                if resolved is not None:
                    hit = {"value": resolved.value, "count": resolved.count}
            except Exception:
                log.debug("pair resolve failed for %r", name, exc_info=True)
        if hit:
            out["pairing"] = hit["value"]
        else:
            out["dropped"].append({"field": "pairing", "name": name})

    name = raw.get("fandom")
    if name:
        hit = None
        # The alias table the extractor already uses: readers (and models)
        # write "Marvel" or "MCU" where the vocabulary has a dozen longer
        # names, and this is where that mapping already lives.
        try:
            row = db.execute(sql_text(
                "SELECT fandom FROM fandom_aliases WHERE lower(alias) = lower(:a) "
                "ORDER BY works DESC LIMIT 1"), {"a": name}).first()
            if row:
                hit = _resolve(db, "fandom", row[0])
        except Exception:
            log.debug("fandom alias failed for %r", name, exc_info=True)
        hit = hit or _resolve(db, "fandom", name)
        if hit:
            out["fandom"] = hit["value"]
        else:
            out["dropped"].append({"field": "fandom", "name": name})

    chars = []
    for name in (raw.get("characters") or [])[:4]:
        hit = _resolve(db, "character", name)
        if hit:
            chars.append(hit["value"])
        else:
            out["dropped"].append({"field": "character", "name": name})
    if chars:
        out["characters"] = chars

    for field in ("wants", "excludes"):
        for item in (raw.get(field) or [])[:8]:
            phrase = (item.get("phrase") or "").strip()
            idea = (item.get("idea") or "").strip()
            if not phrase or not idea:
                continue
            hit = _resolve(db, "tag", idea)
            if hit:
                # The phrase is kept beside the tag. That pairing IS the
                # lesson: it is a phrase-to-tag mapping of exactly the kind
                # tag_hints cannot mine, because mining them statistically
                # needs a sample this machine cannot hold.
                out[field].append({"phrase": phrase, "tag": hit["value"],
                                   "works": hit["count"]})
            else:
                out["dropped"].append({"field": field, "name": idea,
                                       "phrase": phrase})

    for field in ("length", "status", "is_lost_fic"):
        if raw.get(field) is not None:
            out[field] = raw[field]
    return out


CANDIDATES = """
    SELECT p.id, p.title, COALESCE(p.body, '')
      FROM reddit_posts p
     WHERE length(COALESCE(p.body, '')) > 120
       AND NOT EXISTS (SELECT 1 FROM post_labels l WHERE l.post_id = p.id)
     ORDER BY p.posted_at DESC NULLS LAST
     LIMIT :lim
"""


def run(limit: int, dry_run: bool) -> int:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.error("ANTHROPIC_API_KEY is not set — this is the one thing this "
                  "script cannot do without.")
        return 2

    with db_session() as db:
        db.execute(sql_text(DDL))
        db.commit()
        rows = db.execute(sql_text(CANDIDATES), {"lim": limit}).fetchall()

    if not rows:
        log.info("nothing unlabelled")
        return 0
    log.info("%d posts to read", len(rows))

    done = failed = 0
    with httpx.Client() as client:
        for post_id, title, body in rows:
            try:
                raw = label_post(client, api_key, title, body)
            except TeacherRefused as e:
                # Account-level refusals are the same for every post, so
                # grinding through the rest learns nothing and spends the time
                # anyway.
                log.error("stopping: %s", e)
                failed += 1
                break
            except Exception as e:
                log.warning("  %s: %s", post_id, type(e).__name__)
                failed += 1
                time.sleep(GAP_SECONDS)
                continue

            with db_session() as db:
                grounded = _ground(db, raw)
                if dry_run:
                    log.info("  %s", title[:70])
                    log.info("      fandom=%r pairing=%r",
                             grounded.get("fandom"), grounded.get("pairing"))
                    for w in grounded["wants"][:4]:
                        log.info("      want   %-34s <- %r",
                                 w["tag"], w["phrase"][:44])
                    for w in grounded["excludes"][:3]:
                        log.info("      NOT    %-34s <- %r",
                                 w["tag"], w["phrase"][:44])
                    if grounded["dropped"]:
                        log.info("      dropped %s",
                                 [d["name"] for d in grounded["dropped"]][:4])
                else:
                    db.execute(sql_text("""
                        INSERT INTO post_labels (post_id, raw, grounded, model)
                        VALUES (:p, CAST(:r AS jsonb), CAST(:g AS jsonb), :m)
                        ON CONFLICT (post_id) DO UPDATE
                           SET raw = EXCLUDED.raw, grounded = EXCLUDED.grounded,
                               model = EXCLUDED.model, labelled_at = now()
                    """), {"p": post_id, "r": json.dumps(raw),
                           "g": json.dumps(grounded), "m": MODEL})
                    db.commit()
            done += 1
            time.sleep(GAP_SECONDS)

    log.info("DONE — %d labelled, %d failed", done, failed)
    return 0


def load_labels(path: str, dry_run: bool) -> int:
    """Take labels produced somewhere else and put them through the same door.

    The API is one way to get a model to read these posts and it is not the
    only one -- an agent in a session can do it without an API balance, which
    is how this first ran at all. What must NOT vary is what happens next: the
    same grounding, the same vocabulary check, the same refusal to store a name
    this index does not have. A second import path that trusted its input would
    be the hole through which a hallucinated tag reaches the extractor.
    """
    with open(path) as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = [items]

    done = skipped = 0
    with db_session() as db:
        db.execute(sql_text(DDL))
        db.commit()
    for raw in items:
        post_id = (raw or {}).get("post_id")
        if not post_id:
            skipped += 1
            continue
        with db_session() as db:
            grounded = _ground(db, raw)
            if dry_run:
                log.info("  %s  fandom=%r pairing=%r wants=%d excl=%d dropped=%d",
                         post_id, grounded.get("fandom"), grounded.get("pairing"),
                         len(grounded["wants"]), len(grounded["excludes"]),
                         len(grounded["dropped"]))
                for w in grounded["wants"][:3]:
                    log.info("      %-32s <- %r", w["tag"], w["phrase"][:46])
            else:
                db.execute(sql_text("""
                    INSERT INTO post_labels (post_id, raw, grounded, model)
                    VALUES (:p, CAST(:r AS jsonb), CAST(:g AS jsonb), :m)
                    ON CONFLICT (post_id) DO UPDATE
                       SET raw = EXCLUDED.raw, grounded = EXCLUDED.grounded,
                           model = EXCLUDED.model, labelled_at = now()
                """), {"p": post_id, "r": json.dumps(raw),
                       "g": json.dumps(grounded), "m": "agent"})
                db.commit()
            done += 1
    log.info("DONE — %d loaded, %d without a post_id", done, skipped)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true",
                    help="read and ground, print, store nothing")
    ap.add_argument("--teach", action="store_true",
                    help="turn stored labels into tag_hints the extractor reads")
    ap.add_argument("--load", metavar="PATH",
                    help="import labels produced elsewhere (an agent, say) "
                         "through the same grounding the API path uses")
    a = ap.parse_args()
    if a.teach:
        with db_session() as db:
            teach_hints(db, a.dry_run)
        return 0
    if a.load:
        return load_labels(a.load, a.dry_run)
    return run(a.limit, a.dry_run)




# ── turning labels into something the extractor reads ──────────────────────

# Words too ordinary to mean a tag. The phrase a reader used is a sentence, and
# most of its words are grammar; only the distinctive ones can carry a mapping.
_PHRASE_NOISE = {
    "the", "and", "for", "with", "that", "this", "they", "them", "their",
    "she", "her", "his", "him", "was", "were", "had", "has", "have", "been",
    "but", "not", "are", "you", "your", "its", "it's", "from", "there",
    "where", "when", "what", "who", "which", "would", "could", "should",
    "about", "into", "some", "just", "like", "really", "very", "much",
    "more", "most", "also", "then", "than", "other", "only", "even", "still",
    "fic", "fics", "story", "stories", "read", "reading", "looking", "find",
    "remember", "please", "want", "wants", "wanted", "one", "two", "get",
    "gets", "got", "make", "makes", "made", "take", "takes", "goes", "going",
    "something", "someone", "anything", "everything", "character", "main",
    # Added after a teach produced `being -> Found Family`, `after -> Canon
    # Divergence`, `working -> Hurt/Comfort` and `care -> Found Family`. These
    # appear in any sentence about any story and correlate with whichever trope
    # happened to be nearby.
    "being", "after", "before", "during", "while", "working", "works",
    "care", "cares", "life", "lives", "time", "times", "years", "days",
    "back", "down", "away", "over", "through", "around", "together",
    "another", "each", "both", "same", "different", "little", "long",
    "first", "last", "next", "end", "ends", "ending", "start", "starts",
    "becomes", "become", "comes", "come", "know", "knows", "knew",
    "think", "thinks", "thought", "feel", "feels", "felt", "says", "said",
    "tell", "tells", "told", "sees", "seen", "saw", "put", "puts",
}

# How much a taught pair counts beside a mined one. The mined hints carry a
# statistical lift; this is not that number and must not pretend to be. It is
# set so a single taught word is worth less than the summed-lift floor on its
# own -- one word from one post should never place a tag by itself -- and so
# two or three agreeing words do.
TAUGHT_LIFT = float(os.getenv("TEACHER_TAUGHT_LIFT", "6.0"))
# How many distinct posts must use a word for the same tag before it is taught.
# One post is an anecdote: the phrase "he loses his memory and she finds him"
# would otherwise teach `finds` -> Amnesia for ever.
# Raised from two after a teach produced `greenhouse`, `botanical` and
# `garden` all pointing at Hurt/Comfort -- the scenery of a couple of posts,
# not a mapping anybody else's words will match. Three independent readers
# using the same word for the same tag is the point at which it is a shared
# vocabulary rather than a coincidence.
# Two independent posts. Three was tried and left exactly one hint out of 448
# pairs, because 133 posts is not enough for three readers to reach for the
# same word. Two is safe here for a reason that is built in rather than hoped
# for: TAUGHT_LIFT is below the floor _hinted_tags applies to summed lift, so a
# lone taught word cannot place a tag by itself. Scenery like `greenhouse`
# needs corroboration it will never get; a real mapping gets it from the other
# words in the same phrase.
MIN_SUPPORT = int(os.getenv("TEACHER_MIN_SUPPORT", "2"))


def _phrase_words(phrase: str) -> list[str]:
    import re as _re
    return [w for w in _re.findall(r"[a-z']{3,}", (phrase or "").lower())
            if w not in _PHRASE_NOISE]


_CHAR_WORD_CACHE: dict[str, bool] = {}


def _is_character_word(db, word: str) -> bool:
    """Is this word, on its own, a character somebody is written about?

    The per-post exclusion catches a name the label RESOLVED -- but a post
    whose pairing or characters came back empty still has its names in the
    phrases, and they leaked straight through: `sirius -> Canon Divergence`,
    `aizawa -> Found Family`, `batman's -> Deconstruction`. A name correlates
    with whatever trope its post happened to want, and the extractor resolves
    names properly by itself, so a hint on one can only add noise.

    Asked of the vocabulary rather than a word list, and cached, because the
    alternative -- tag_hints' 46,181-word name set -- contains ordinary English
    and removed `canon` and `brothers` along with the names.
    """
    # A possessive SUFFIX, not a set of characters to strip from both ends.
    # str.strip("'s") removes every leading and trailing ' and s, so "sirius"
    # arrived here as "iriu" and matched no character at all -- which is how
    # `sirius -> Canon Divergence` survived the check written to stop it, while
    # `aizawa` was caught and looked like proof the check worked.
    key = word[:-2] if word.endswith("'s") else word
    if key in _CHAR_WORD_CACHE:
        return _CHAR_WORD_CACHE[key]
    try:
        # A WORD WITHIN a character's name, not the whole name.
        #
        # Exact matching caught nothing useful: the vocabulary holds `Sirius
        # Black` and `Aizawa Shouta`, never the bare first name a reader writes
        # -- so `sirius -> Canon Divergence` and `aizawa -> Found Family`
        # survived a check that was supposed to stop exactly them.
        #
        # The regex is anchored on word boundaries so `art` does not match
        # `Bartholomew`, and the count floor keeps it to characters enough
        # people write for the name to be worth protecting.
        hit = bool(db.execute(sql_text(
            "SELECT 1 FROM facets WHERE kind = 'character' AND count >= :c "
            r"AND value ~* ('\m' || :w || '\M') LIMIT 1"),
            {"w": key, "c": 2000}).first())
    except Exception:
        hit = False
    _CHAR_WORD_CACHE[key] = hit
    return hit


def teach_hints(db, dry_run: bool = False) -> int:
    """Turn stored labels into tag_hints rows the extractor already reads.

    `_hinted_tags` looks up single lowercase words and sums their lift, so a
    phrase teaches several words that point at one tag and reinforce each
    other. That is why the phrase had to be recorded verbatim beside every
    want: without it there is nothing to key on.

    This is the whole point of the exercise. tag_hints already maps what
    readers SAY to what archives TAG, mined statistically from summaries -- but
    only one word at a time, because mining pairs needs a sample this machine
    cannot hold. A labelled post gives the mapping directly, from the words a
    reader actually used, with no sample size at all.
    """
    import collections
    rows = db.execute(sql_text(
        "SELECT post_id, grounded FROM post_labels")).fetchall()

    support: dict[tuple, set] = collections.defaultdict(set)
    for post_id, grounded in rows:
        # The names THIS post is about, taken from what it already resolved to.
        #
        # Without this the first teach produced `harry -> Canon Divergence`,
        # `steve -> Hurt/Comfort` and `harry -> Alternate Universe`: a name
        # appears in every phrase about that character, so it correlates with
        # whatever trope the post happened to want. The extractor already
        # resolves names properly, so a hint on one adds noise to a term it
        # would have found anyway.
        #
        # Per post, NOT against tag_hints' global name list. That list holds
        # 46,181 words and necessarily contains ordinary English -- filtering
        # by it removed `canon` and `brothers` along with `harry`, and taught
        # nothing at all.
        g = grounded or {}
        own = " ".join([str(g.get("fandom") or ""), str(g.get("pairing") or "")]
                       + [str(c) for c in (g.get("characters") or [])]).lower()
        own_words = {w for w in _phrase_words(own)}
        for field in ("wants", "excludes"):
            for item in (grounded or {}).get(field) or []:
                tag = item.get("tag")
                for w in _phrase_words(item.get("phrase")):
                    if w in own_words or _is_character_word(db, w):
                        continue
                    support[(w, tag)].add(post_id)

    keep = [{"w": w, "t": t, "l": TAUGHT_LIFT * len(posts), "d": len(posts)}
            for (w, t), posts in support.items()
            if len(posts) >= MIN_SUPPORT]

    log.info("labels: %d pairs seen, %d taught (support >= %d)",
             len(support), len(keep), MIN_SUPPORT)
    if dry_run or not keep:
        for k in sorted(keep, key=lambda x: -x["d"])[:20]:
            log.info("    %-18s -> %-34s posts %d", k["w"], k["t"], k["d"])
        return len(keep)

    db.execute(sql_text("""
        INSERT INTO tag_hints (word, tag, lift, docs)
        VALUES (:w, :t, :l, :d)
        ON CONFLICT (word, tag) DO UPDATE
           SET lift = GREATEST(tag_hints.lift, EXCLUDED.lift),
               docs = GREATEST(tag_hints.docs, EXCLUDED.docs),
               built_at = now()
    """), keep)
    db.commit()
    return len(keep)


if __name__ == "__main__":
    sys.exit(main())
