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
