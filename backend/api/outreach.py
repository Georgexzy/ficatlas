"""Outreach beyond answering posts: a channels playbook and a weekly digest.

The worklist (api/queue.py) answers people who are already asking. These are
the other strategies, from the research recorded in outreach_sources.py and
below — each one a thing a PERSON does, because every channel here is a
community that judges whoever turns up, and none of them welcomes automation.

  /channels   the researched list, with a status the operator sets (todo,
              doing, done, not doing) and a note, kept in app_settings.
  /digest     "most recommended this week": works several different people
              recommended in the fic communities read by rec_threads, drafted
              as a Bluesky thread and a Tumblr post. Content people want to
              follow, with the site as the place the links go — rather than an
              advert. Gated exactly as the hubs are: it is a public post.

Owner-only, like the rest of the admin panel.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import require_admin
from api.search import (_ADULT_TAGS, _ADULT_WARNINGS,
                        _UNDERAGE_TAGS, _UNDERAGE_WARNINGS)
from db.session import get_db

router = APIRouter()

SITE = "https://ficatlas.com"
_KEY = "outreach_channels"
_STATUSES = ("todo", "doing", "done", "skip")

# Researched 2026-10-05. `why` is the argument for doing it; `how` is the
# smallest correct first step; `avoid` is how it goes wrong.
CHANNELS: list[dict] = [
    {"id": "fanlore", "name": "Fanlore",
     "url": "https://fanlore.org/wiki/Searching_for_Fanworks_on_the_Internet",
     "why": "The OTW's own wiki documents fan-made tools and is where fans "
            "look up how to search for fic. A factual entry is in scope there.",
     "how": "Create a short, neutral page for FicAtlas (what it indexes, that "
            "it links to the archives and hosts nothing without consent) and "
            "add it to the search page. Say on the talk page that you made it.",
     "avoid": "Marketing language. Fanlore edits are reviewed by fans and an "
              "advert is reverted."},
    {"id": "reddit-wikis", "name": "Subreddit resource wikis",
     "url": "https://www.reddit.com/r/FanFiction/wiki/index",
     "why": "r/FanFiction, r/AO3 and r/HPFanfiction keep resource lists that "
            "newcomers are pointed to, which is a standing link rather than "
            "one reply.",
     "how": "Message the mods once, through modmail, with what it does and "
            "that it is non-commercial. Ask; do not post.",
     "avoid": "Posting it as a thread. Self-promotion rules differ per sub "
              "and change."},
    {"id": "tumblr-libraries", "name": "Tumblr fic-library blogs",
     "url": "https://www.tumblr.com/tagged/fic%20finder",
     "why": "Most Tumblr fic asks go to a handful of library blogs, who answer "
            "dozens a week by hand. A tool that searches three archives at "
            "once saves them time, and they are the ones answering.",
     "how": "One short message to a blog that is visibly answering asks (the "
            "Tumblr posts in the worklist show who). Offer, do not ask for a "
            "shout-out.",
     "avoid": "Messaging many blogs at once, or the same blog twice."},
    {"id": "bluesky-digest", "name": "A weekly 'most recommended' post",
     "url": "https://bsky.app",
     "why": "An account that posts what fandom is recommending this week is "
            "something people follow for its own sake — the links happen to "
            "go here. The digest below drafts it from real recommendations.",
     "how": "Post the drafted thread from a FicAtlas account once a week, "
            "same day. Reply to anyone who asks how it is made.",
     "avoid": "Posting more than weekly, or filling a quiet week with filler."},
    {"id": "discord", "name": "Fandom Discord servers",
     "url": "https://discord.com",
     "why": "Many fandoms live in Discord now and most servers have a "
            "#fic-recs channel where the same questions are asked.",
     "how": "Only in servers you already belong to. Answer a rec request with "
            "the work itself and the search link, as on Reddit.",
     "avoid": "Joining servers to post the link. Most server rules ban it "
              "outright."},
    {"id": "alternativeto", "name": "AlternativeTo listing",
     "url": "https://alternativeto.net",
     "why": "People search 'AO3 alternatives' and 'FanFiction.net search' "
            "there, and a listing is a permanent inbound link.",
     "how": "List FicAtlas as an alternative to Archive of Our Own and "
            "FanFiction.net search, honestly described as an index.",
     "avoid": "Describing it as a replacement for the archives. It is not."},
    {"id": "search-console", "name": "Google Search Console and Bing",
     "url": "https://search.google.com/search-console",
     "why": "The hubs are built for search engines, and neither has been told "
            "the sitemap exists. Bing also feeds DuckDuckGo and others.",
     "how": "Verify the domain in both, submit /sitemap.xml.",
     "avoid": "Nothing — this one only needs doing."},
    {"id": "no-ao3-comments", "name": "Not: AO3 comments or tags",
     "url": "https://archiveofourown.org/tos",
     "why": "Listed so it is decided once. AO3's terms and its readers treat "
            "promotion in comments and tags as spam, and the archive is the "
            "one partner this site cannot afford to annoy.",
     "how": "Mark as 'not doing'.",
     "avoid": "Any of it."},
]


class ChannelState(BaseModel):
    status: str
    note: Optional[str] = None


def _states(db: Session) -> dict:
    raw = db.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                     {"k": _KEY}).scalar()
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


@router.get("/channels")
def channels(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    st = _states(db)
    return [{**c, **st.get(c["id"], {"status": "todo", "note": None, "at": None})}
            for c in CHANNELS]


@router.post("/channels/{cid}")
def set_channel(cid: str, body: ChannelState, db: Session = Depends(get_db),
                _admin=Depends(require_admin)):
    if cid not in {c["id"] for c in CHANNELS}:
        raise HTTPException(404, "No such channel.")
    if body.status not in _STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(_STATUSES)}")
    from api.settings import put_setting
    st = _states(db)
    st[cid] = {"status": body.status, "note": (body.note or "")[:500] or None,
               "at": int(time.time())}
    put_setting(db, _KEY, json.dumps(st))
    return st[cid]


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


@router.get("/digest")
def digest(days: int = 7, min_people: int = 2, limit: int = 8,
           db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """This week's most-recommended works, drafted for posting.

    Gated like a hub, because a public post is a no-toggle surface: neither
    tier's works may appear, by column or by tag. Recommended by at least
    `min_people` different people — one person's list is not "fandom is
    recommending".
    """
    days = max(1, min(days, 90))
    rows = db.execute(text("""
        SELECT s.id::text, s.title, s.author, s.fandoms[1], s.site::text,
               count(DISTINCT m.recommender) AS people
          FROM rec_mentions m JOIN stories s ON s.id = m.story_id
         WHERE m.mentioned_at > now() - make_interval(days => :days)
           AND s.delisted_at IS NULL
           AND NOT s.gate_underage AND NOT s.gate_adult
           AND NOT (COALESCE(s.warnings,'{}') && CAST(:uw AS text[]))
           AND NOT (COALESCE(s.tags,'{}')     && CAST(:ut AS text[]))
           AND NOT (COALESCE(s.warnings,'{}') && CAST(:aw AS text[]))
           AND NOT (COALESCE(s.tags,'{}')     && CAST(:at AS text[]))
         GROUP BY 1, 2, 3, 4, 5
        HAVING count(DISTINCT m.recommender) >= :need
         ORDER BY people DESC, max(m.mentioned_at) DESC
         LIMIT :lim
    """), {"days": days, "need": max(1, min_people), "lim": max(1, min(limit, 20)),
           "uw": _UNDERAGE_WARNINGS, "ut": _UNDERAGE_TAGS,
           "aw": _ADULT_WARNINGS, "at": _ADULT_TAGS}).fetchall()
    def _fandom(f):
        # AO3's naming furniture is noise in a post: "Harry Potter - J. K.
        # Rowling" is "Harry Potter", "原神 | Genshin Impact" is the last half.
        f = (f or "").split(" | ")[-1]
        return f.split(" - ")[0].strip() or None
    works = [{"id": r[0], "title": r[1], "author": r[2], "fandom": _fandom(r[3]),
              "site": r[4], "people": r[5], "url": f"{SITE}/story/{r[0]}"}
             for r in rows]
    if not works:
        return {"days": days, "works": [], "bluesky": [], "tumblr": None}

    span = "this week" if days == 7 else f"in the last {days} days"
    intro = (f"What fanfic readers recommended most {span}, counted across "
             f"Reddit's fic communities — each one by at least {min_people} "
             f"different people. 🧵")
    posts = [_clip(intro, 300)]
    for i, w in enumerate(works, 1):
        tail = f"\n{w['url']}"
        head = (f"{i}. {w['title']}" + (f" by {w['author']}" if w['author'] else "")
                + (f" ({w['fandom']})" if w['fandom'] else "")
                + f" — recommended by {w['people']} people")
        posts.append(_clip(head, 300 - len(tail)) + tail)
    tumblr = (f"**Most recommended fic {span}**\n\n"
              + "\n".join(
                  f"- [{w['title']}]({w['url']})"
                  + (f" by {w['author']}" if w['author'] else "")
                  + (f" · {w['fandom']}" if w['fandom'] else "")
                  + f" · recommended by {w['people']} people"
                  for w in works)
              + "\n\nCounted from recommendation threads; links go to an index "
                "that points you to the archive each one is on.")
    return {"days": days, "works": works, "bluesky": posts, "tumblr": tumblr}
