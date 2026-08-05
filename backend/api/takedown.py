"""
Takedown requests for rehosted story text.
==========================================

FicAtlas indexes ~19.7M works as metadata and hosts the full text of about
30,000 of them — mostly from FicAlley and HPFFA, archives that closed. Indexing
metadata is what a search engine does. Rehosting someone's complete story is a
different thing, and those authors posted to those archives, not here.

So there has to be a way to ask for text to come down, and it has to be usable
by someone who is not technical and is probably annoyed.

The policy, and why:

  A request HIDES the text immediately, automatically, before any human looks.

    Erring toward removal costs almost nothing — the work is not ours, and the
    metadata entry (title, author, tags, link to the original) survives, so the
    index stays useful and the author stays findable. Making a distressed author
    wait days for a reply is the failure mode worth avoiding.

  A request NEVER deletes anything, and is always reversible.

    This is what makes auto-hiding safe. If auto-removal were destructive, the
    form would be a way for anyone to permanently erase other people's work —
    a griefing vector aimed at exactly the content we are trying to protect.
    Hidden is a flag; a human decides whether it becomes permanent.

  Rate-limited and IP-stamped.

    Auto-hide is only defensible if mass abuse is hard. ratelimit.py caps this
    path, and every request records its source address.

There is deliberately NO automatic judgement of validity. "Is this person really
the author?" cannot be settled by a form — anyone can type a name. The one
signal that IS reliable is the author deleting the work at its source, which
worker.py checks separately (see _withdraw_deleted_sources): if a work has
vanished from AO3 or FFN, its author has withdrawn it, and our copy goes too
without anyone having to ask.
"""

import logging
import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from db.session import get_db
from models.story import Story
from models.user import User
from api.auth import require_admin

log = logging.getLogger(__name__)
router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RELATIONSHIPS = {"author", "agent", "other"}

# Site-wide ceiling on AUTOMATIC hiding, per rolling hour.
#
# The per-IP rate limit caps one attacker at 10/min. That is fine against
# hammering and useless against patience: 14,400 a day from a single address,
# and the whole 30,000-work hosted corpus hidden inside 50 hours — faster from
# several addresses, which behind Cloudflare is trivial to arrange.
#
# The answer cannot be to verify claimants. Fandom is pseudonymous by design —
# people write under a pen name precisely to keep it apart from their identity —
# so demanding proof of authorship is both a serious breach of that norm and
# useless here: most hosted works came from FicAlley, an archive that no longer
# exists, so there is no account left for anyone to prove control of.
#
# So the protection is structural instead. Real takedowns arrive in ones and
# twos; a burst is abuse by definition. Past this ceiling requests are still
# RECORDED and still reach an admin, they simply stop hiding anything by
# themselves. A genuine author in an unlucky hour waits for a human rather than
# being turned away, and an attacker gets a queue instead of a weapon.
AUTOHIDE_MAX_PER_HOUR = int(os.getenv("TAKEDOWN_AUTOHIDE_PER_HOUR", "25"))

_RECENT_AUTOHIDES = sql_text("""
    SELECT count(*) FROM takedowns
    WHERE created_at > now() - interval '1 hour' AND story_id IS NOT NULL
""")


class TakedownOut(BaseModel):
    id: str
    story_url: str
    claimant: str
    email: str
    relationship: str
    detail: str | None
    state: str
    created_at: str
    story_title: str | None = None
    story_id: str | None = None
    # What is actually true of the story right now, so the queue shows the
    # CURRENT state rather than what was asked for. A request can be superseded
    # — another one for the same work, or an admin acting directly — and an
    # operator deciding whether to reverse something needs to see where it
    # stands, not only what the form said when it arrived.
    text_hidden: bool = False
    delisted: bool = False
    is_hosted: bool = False
    source_url: str | None = None


@router.post("")
async def submit_takedown(
    request: Request,
    story_url: str = Form(...),
    claimant: str = Form(...),
    email: str = Form(...),
    relationship: str = Form("author"),
    detail: str = Form(""),
    delist: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Accept a takedown request and hide the story's text immediately."""
    story_url = story_url.strip()
    claimant = claimant.strip()
    email = email.strip().lower()

    if not story_url:
        raise HTTPException(400, "Please include the address of the story.")
    if not (2 <= len(claimant) <= 120):
        raise HTTPException(400, "Please include your name.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please include an email address we can reply to.")
    if relationship not in RELATIONSHIPS:
        relationship = "other"

    # Accept either a FicAtlas URL or the original archive URL — an author knows
    # their own AO3 link and should not have to hunt for ours.
    story = None
    m = re.search(r"/story/([0-9a-fA-F-]{36})", story_url)
    if m:
        story = db.query(Story).filter(Story.id == m.group(1)).first()
    if story is None:
        story = db.query(Story).filter(Story.url == story_url).first()
    if story is None:
        m = re.search(r"(archiveofourown\.org/works/\d+|fanfiction\.net/s/\d+)", story_url)
        if m:
            story = (db.query(Story)
                     .filter(Story.url.ilike(f"%{m.group(1)}%"))
                     .first())

    hidden = False
    delisted = False
    throttled = False
    if story is not None:
        recent = db.execute(_RECENT_AUTOHIDES).scalar() or 0
        if recent >= AUTOHIDE_MAX_PER_HOUR:
            throttled = True
            log.warning(f"takedown auto-hide ceiling reached ({recent} in the last "
                        f"hour) — recording without acting: {story_url[:120]}")
        else:
            if story.is_hosted and story.text_withdrawn_at is None:
                story.text_withdrawn_at = datetime.utcnow()
                story.text_withdrawn_reason = "takedown request"
                hidden = True
            # Delisting is opt-in, because it is not the better answer by
            # default. The listing is a title, an author and a link to where the
            # work actually lives, so leaving it keeps the author findable —
            # removing it makes them harder to find, which is rarely what
            # someone objecting to a rehost actually wants. But the form has
            # always offered it, and an author who asks has a reason.
            #
            # Auto-applied like the text hide, and for the same reason: making
            # someone wait days for the specific thing they asked for is the
            # failure mode auto-action exists to avoid. Reversible, under the
            # same hourly ceiling, and never a delete.
            if delist and story.delisted_at is None:
                story.delisted_at = datetime.utcnow()
                story.delisted_reason = "takedown request"
                delisted = True

    db.execute(sql_text("""
        INSERT INTO takedowns (story_id, story_url, claimant, email, relationship,
                               detail, source_ip)
        VALUES (:sid, :url, :who, :mail, :rel, :detail, :ip)
    """), {
        "sid": str(story.id) if story else None,
        "url": story_url[:2000],
        "who": claimant[:120],
        "mail": email[:200],
        "rel": relationship,
        "detail": (detail or "").strip()[:4000] or None,
        "ip": (request.client.host if request.client else None),
    })
    db.commit()

    log.info(f"takedown submitted for {story_url[:120]} "
             f"(matched={'yes' if story else 'no'}, hidden={hidden}, "
             f"delisted={delisted})")

    return {
        "received": True,
        "matched": story is not None,
        "hidden": hidden,
        "delisted": delisted,
        # Told plainly, because the person submitting this wants to know it
        # actually did something, not that it entered a queue.
        "message": (
            "The story has been removed from FicAtlas — the text is gone and the "
            "listing no longer appears in search."
            if hidden and delisted else
            "The listing has been removed and no longer appears in search."
            if delisted else
            "The story text has been taken down and is no longer readable here."
            if hidden else
            "Your request has been recorded and a person will action it shortly. "
            "An unusual number of removal requests have come in this hour, so "
            "this one is being checked by hand."
            if throttled else
            "Your request has been recorded and we will look at it."
        ),
    }


@router.get("/check")
async def check_author(author: str = "", db: Session = Depends(get_db)):
    """Let an author see, without asking anyone, whether their work is hosted here.

    Takedown alone is a reactive remedy: it only helps someone who has already
    discovered the site, found the form and decided to act. For metadata that is
    proportionate — indexing a title and linking to the original is what a search
    engine does. For the ~30,000 works whose full TEXT is served here it is
    weaker, because the burden of discovery sits entirely with the author.

    What r/FanFiction objects to, in its own words, is work "being stolen en
    masse... for profit, no less" — the trigger is redistribution of full text
    for money. FicAtlas takes no money, but it does hold the text, and "we would
    have removed it if only you had known to ask" is a poor answer to someone
    who never knew.

    So: search by pen name, see exactly what is readable here, remove it in one
    step. Deliberately public and unauthenticated — requiring an account to find
    out whether your own work is being hosted would defeat the purpose.

    Returns only works whose TEXT we hold. Metadata-only rows are not listed,
    because those are a link to the author's own copy, not a copy of it.
    """
    author = (author or "").strip()
    if len(author) < 2:
        return {"author": author, "hosted": [], "searched": False}

    rows = db.execute(sql_text("""
        SELECT id, title, url, site, text_withdrawn_at IS NOT NULL AS withdrawn
        FROM stories
        WHERE is_hosted AND lower(author) = lower(:a)
        ORDER BY title LIMIT 200
    """), {"a": author}).fetchall()

    return {
        "author": author,
        "searched": True,
        "hosted": [{"id": str(r[0]), "title": r[1], "url": r[2],
                    "site": r[3], "withdrawn": r[4]} for r in rows],
    }


# ── admin ───────────────────────────────────────────────────────────────────

@router.get("", response_model=list[TakedownOut])
async def list_takedowns(state: str = "pending", limit: int = 100,
                         db: Session = Depends(get_db),
                         _admin: User = Depends(require_admin)):
    rows = db.execute(sql_text("""
        SELECT t.id, t.story_url, t.claimant, t.email, t.relationship, t.detail,
               t.state, t.created_at, s.title, t.story_id,
               s.text_withdrawn_at IS NOT NULL, s.delisted_at IS NOT NULL,
               COALESCE(s.is_hosted, false), s.url
        FROM takedowns t
        LEFT JOIN stories s ON s.id = t.story_id
        WHERE (:state = 'all' OR t.state = :state)
        ORDER BY t.created_at DESC LIMIT :lim
    """), {"state": state, "lim": min(limit, 500)}).fetchall()
    return [TakedownOut(
        id=str(r[0]), story_url=r[1], claimant=r[2], email=r[3], relationship=r[4],
        detail=r[5], state=r[6], created_at=r[7].isoformat(),
        story_title=r[8], story_id=str(r[9]) if r[9] else None,
        text_hidden=bool(r[10]), delisted=bool(r[11]),
        is_hosted=bool(r[12]), source_url=r[13],
    ) for r in rows]


@router.get("/pending-count")
async def pending_count(db: Session = Depends(get_db),
                        _admin: User = Depends(require_admin)):
    """How many requests are waiting, for the badge on the admin entry point.

    Auto-hiding means nothing is URGENT — the text is already down before anyone
    looks — but a queue nobody knows about is a queue nobody empties, and every
    one of these is a person waiting to be told what happened.
    """
    n = db.execute(sql_text(
        "SELECT count(*) FROM takedowns WHERE state = 'pending'")).scalar() or 0
    return {"pending": n}


@router.post("/{takedown_id}/resolve")
async def resolve_takedown(takedown_id: str, uphold: bool = Form(True),
                           note: str = Form(""), delist: bool = Form(False),
                           db: Session = Depends(get_db),
                           admin: User = Depends(require_admin)):
    """Uphold (text stays down) or reject (text comes back)."""
    row = db.execute(sql_text("SELECT story_id, state FROM takedowns WHERE id = :i"),
                     {"i": takedown_id}).first()
    if not row:
        raise HTTPException(404, "No such takedown request")

    if row[0]:
        story = db.query(Story).filter(Story.id == row[0]).first()
        if story:
            if uphold:
                story.text_withdrawn_at = story.text_withdrawn_at or datetime.utcnow()
                story.text_withdrawn_reason = "takedown upheld"
                if delist:
                    story.delisted_at = story.delisted_at or datetime.utcnow()
                    story.delisted_reason = "takedown upheld"
            else:
                # Rejecting restores BOTH, so "reject" means what it says. Leaving
                # a delisting in place after rejecting the request would make a
                # work unfindable with no record of anyone having decided that.
                story.text_withdrawn_at = None
                story.text_withdrawn_reason = None
                story.delisted_at = None
                story.delisted_reason = None

    db.execute(sql_text("""
        UPDATE takedowns SET state = :st, resolved_at = now(), resolved_by = :by,
               note = :note WHERE id = :i
    """), {"st": "upheld" if uphold else "rejected", "by": str(admin.id),
           "note": (note or "").strip()[:2000] or None, "i": takedown_id})
    db.commit()
    return {"ok": True, "state": "upheld" if uphold else "rejected"}
