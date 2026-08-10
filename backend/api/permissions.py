"""
Authors granting — and withdrawing — permission for their work to be hosted.
============================================================================

Companion to api/takedown.py, and deliberately shaped differently from it.

    Removal        no proof, acts immediately, reversible.  (takedown.py)
    Permission     proof required, acts on future ingest, revocable.  (here)

That asymmetry is the whole design. Getting a removal wrong hides a work that
need not have been hidden, which is recoverable and inconveniences the person
who asked for it. Getting a permission wrong means hosting someone's writing
without their consent — not recoverable by the person it happens to, who may
never find out. So the cheap path is the one that removes, and the path that
needs evidence is the one that permits.

Nothing here can be used to remove anything. If someone wants their work down
they use the takedown form, which asks nothing of them. Requiring an author to
prove who they are before they may object would be exactly backwards.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import author_permission as ap
from db.session import get_db
from api.auth import require_admin

log = logging.getLogger(__name__)
router = APIRouter()


class ChallengeOut(BaseModel):
    token: str
    site: str
    author: str
    profile_url: str
    expires_in_hours: int


class PermissionOut(BaseModel):
    site: str
    author: str
    author_display: str | None = None
    policy: str
    verified_at: str


def _check_site(site: str) -> str:
    s = (site or "").strip().lower()
    if s not in ap.SITES:
        raise HTTPException(400, "Only AO3 and FanFiction.net can be verified.")
    return s


@router.post("/challenge", response_model=ChallengeOut)
def start_challenge(request: Request,
                    site: str = Form(...),
                    author: str = Form(...),
                    db: Session = Depends(get_db)):
    """Issue a one-time token for the author to place in their own profile."""
    site = _check_site(site)
    author = (author or "").strip()
    if not author:
        raise HTTPException(400, "Tell us your username on that archive.")

    if site not in ap.VERIFIABLE_SITES:
        raise HTTPException(400,
            "FanFiction.net can't be verified this way — it blocks automated "
            "requests, so we cannot read your profile to check a code. You can "
            "still restrict what we do with your work without proving anything, "
            "and you can always use the removal form.")

    url = ap.profile_url(site, author)
    if not url:
        raise HTTPException(400,
            "That does not look like a profile. For FanFiction.net use your "
            "profile's numeric id, or paste the whole profile link.")

    # Opportunistic cleanup, so expired rows do not accumulate. Cheap, indexed,
    # and this is the only endpoint that creates them.
    try:
        ap.purge_expired_challenges(db)
    except Exception as e:                       # never fail issuing over tidying
        log.warning("challenge purge failed: %s: %s", type(e).__name__, e)

    token = ap.create_challenge(db, site, author, _client_ip(request))
    return ChallengeOut(
        token=token, site=site, author=author, profile_url=url,
        expires_in_hours=int(ap.CHALLENGE_TTL.total_seconds() // 3600),
    )


@router.post("/verify", response_model=PermissionOut)
def verify(request: Request,
           token: str = Form(...),
           policy: str = Form(...),
           email: str = Form(""),
           db: Session = Depends(get_db)):
    """Read the author's public profile and confirm the token is on it."""
    ch = ap.load_challenge(db, (token or "").strip())
    if not ch:
        raise HTTPException(404,
            "That verification has expired or was already used. Start again to "
            "get a fresh code.")

    from datetime import datetime, timezone
    expires = ch["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        ap.consume_challenge(db, ch["token"])
        raise HTTPException(410, "That code has expired. Start again for a new one.")

    # Bounded retries: this endpoint causes an outbound fetch to the archive, so
    # it must not be usable to hammer them on our behalf.
    if ch["attempts"] >= ap.MAX_ATTEMPTS:
        raise HTTPException(429,
            "Too many checks for this code. Start again to get a fresh one.")
    ap.bump_attempts(db, ch["token"])

    policy = (policy or "").strip().lower()
    if policy not in ap.POLICIES:
        raise HTTPException(400, "Choose what you are allowing.")

    url = ap.profile_url(ch["site"], ch["author"])
    try:
        page = ap.fetch_profile(url)
    except ap.VerificationError as e:
        raise HTTPException(502, str(e))

    if not ap.token_present(page, ch["token"]):
        raise HTTPException(400,
            "The code is not on that profile yet. Paste it anywhere in your "
            "profile, save, then check again — archives sometimes take a moment "
            "to show the change.")

    ap.record_permission(
        db, site=ch["site"], author=ch["author"], author_display=ch.get("author_display") or ch["author"],
        policy=policy, token=ch["token"], evidence_url=url,
        evidence_text=ap.extract_evidence(page, ch["token"]),
        contact_email=(email or "").strip() or None,
    )
    ap.consume_challenge(db, ch["token"])
    log.info("author permission recorded: %s/%s -> %s", ch["site"], ch["author"], policy)

    got = ap.get_permission(db, ch["site"], ch["author"])
    return PermissionOut(
        site=got["site"], author=got["author"],
        author_display=got.get("author_display"),
        policy=got["policy"], verified_at=str(got["verified_at"]),
    )


@router.post("/restrict")
def restrict(request: Request, site: str = Form(...), author: str = Form(...),
             policy: str = Form(...), db: Session = Depends(get_db)):
    """Reduce what FicAtlas may do with an author's work, without verification.

    Only the restrictive policies are reachable here, and that is the whole
    point. Proof exists to stop someone licensing writing that is not theirs; it
    has nothing to protect against when the statement only ever takes permission
    away. The worst an unverified "deny" achieves is that we stop indexing a work
    we were allowed to index — an outcome its author could demand through the
    takedown form at any time, also without proof.

    This is what keeps FanFiction.net authors from being shut out entirely. They
    cannot grant hosting, because their profiles cannot be read (see
    VERIFIABLE_SITES), but the direction that matters most to someone who has
    just found their work somewhere unexpected is open to them.
    """
    site = _check_site(site)
    policy = (policy or "").strip().lower()
    if policy not in ap.RESTRICTIVE_POLICIES:
        raise HTTPException(400,
            "Only restrictions can be set without verifying. To let FicAtlas "
            "host your work, verify your account first.")
    if not (author or "").strip():
        raise HTTPException(400, "Tell us the name your work is published under.")

    ap.record_permission(
        db, site=site, author=author, author_display=(author or "").strip(),
        policy=policy, token=None, evidence_url=None,
        evidence_text=None, contact_email=None,
        # Recorded as what it is. Calling an unverified restriction
        # "profile_token" in the audit trail would make the one column that says
        # how much to trust a row lie about exactly the rows worth checking.
        method="unverified_restriction", source_ip=_client_ip(request),
    )
    log.info("author restriction recorded (unverified): %s/%s -> %s",
             site, author, policy)
    return {"ok": True, "site": site, "author": author.strip(), "policy": policy}


@router.get("/lookup")
def lookup(site: str, author: str, db: Session = Depends(get_db)):
    """What has this author said, if anything.

    Public because it is the author's own statement about their own work, and
    because being able to check it is what makes the evidence meaningful. It
    exposes no address — contact_email is deliberately not returned.
    """
    site = _check_site(site)
    got = ap.get_permission(db, site, author)
    if not got:
        return {"found": False, "policy": None}
    return {"found": True, "policy": got["policy"],
            "author": got.get("author_display") or got["author"],
            "verified_at": str(got["verified_at"])}


@router.post("/revoke")
def revoke(site: str = Form(...), author: str = Form(...),
           token: str = Form(""), db: Session = Depends(get_db)):
    """Withdraw a permission.

    Verification is NOT required, and that is on purpose. Revoking only ever
    reduces what we may do, so an unverified revocation cannot be used to take
    anything that is not the revoker's to take — the worst it achieves is
    stopping us hosting a work we were permitted to host, which is the outcome
    the author can always demand anyway through the takedown form.

    Requiring proof here would mean an author who lost access to their old
    archive account could never withdraw consent, which is precisely backwards.
    Removal of text already published is the takedown form; this stops future
    ingest.
    """
    site = _check_site(site)
    changed = ap.revoke_permission(db, site, author)
    log.info("author permission revoked: %s/%s (existed=%s)", site, author, changed)
    return {"ok": True, "revoked": changed}


@router.get("/works")
def my_works(site: str, author: str, limit: int = 200,
             db: Session = Depends(get_db)):
    """Everything FicAtlas holds under this name, and what state each work is in.

    Public, and that is deliberate: it lists nothing that is not already on the
    author's own results page, and requiring a login to see what a site holds
    about you would be a strange thing to ask of someone who has just found out
    it holds anything at all.
    """
    site = _check_site(site)
    from sqlalchemy import text as sql_text
    rows = db.execute(sql_text("""
        SELECT id::text, title, url, is_hosted,
               text_withdrawn_at IS NOT NULL AS text_withdrawn,
               delisted_at IS NOT NULL AS delisted,
               word_count, chapter_count
        FROM stories
        WHERE site = :s AND lower(author) = :a
        ORDER BY coalesce(updated_at, published_at) DESC NULLS LAST
        LIMIT :l
    """), {"s": site, "a": ap.normalise_author(author),
           "l": max(1, min(limit, 500))}).mappings().all()
    perm = ap.get_permission(db, site, author)
    return {
        "site": site,
        "author": (perm or {}).get("author_display") or author,
        "policy": (perm or {}).get("policy"),
        "verified": bool(perm),
        "count": len(rows),
        "works": [dict(r) for r in rows],
    }


@router.post("/works/{story_id}/withdraw")
def withdraw_work(story_id: str, delist: bool = Form(False),
                  db: Session = Depends(get_db)):
    """Take one work down, without proving anything.

    Same rule as everywhere else here: removing needs no proof. This is the
    per-work version of the takedown form and behaves identically — the text is
    hidden immediately, nothing is deleted, and it can be undone. `delist` also
    hides the listing for an author who wants their name off the index entirely.
    """
    from sqlalchemy import text as sql_text
    r = db.execute(sql_text("""
        UPDATE stories
           SET text_withdrawn_at = now(),
               text_withdrawn_reason = 'author request (self-service)',
               delisted_at = CASE WHEN :d THEN now() ELSE delisted_at END,
               delisted_reason = CASE WHEN :d THEN 'author request (self-service)'
                                      ELSE delisted_reason END
         WHERE id = CAST(:i AS uuid)
    """), {"i": story_id, "d": bool(delist)})
    db.commit()
    if not r.rowcount:
        raise HTTPException(404, "No such work.")
    log.info("self-service withdrawal: story=%s delist=%s", story_id, delist)
    return {"ok": True, "delisted": bool(delist)}


@router.get("/admin/list")
def admin_list(limit: int = 200, db: Session = Depends(get_db),
               _admin=Depends(require_admin)):
    from sqlalchemy import text as sql_text
    rows = db.execute(sql_text("""
        SELECT site, author, author_display, policy, verified_at, revoked_at,
               evidence_url, left(coalesce(evidence_text,''), 300) AS evidence
        FROM author_permissions
        ORDER BY updated_at DESC LIMIT :l
    """), {"l": max(1, min(limit, 1000))}).mappings().all()
    return [dict(r) for r in rows]


def _client_ip(request: Request) -> str | None:
    try:
        from ratelimit import client_ip
        return client_ip(request)
    except Exception:
        return request.client.host if request.client else None
