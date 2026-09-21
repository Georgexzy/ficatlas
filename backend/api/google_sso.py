"""Sign in with Google — for a new account, or attached to one that exists.

    GET  /api/auth/google/start      -> redirect to Google
    GET  /api/auth/google/callback   -> exchange, sign in, come back
    POST /api/auth/google/unlink     -> detach it again

Why
---
The site has one owner and one reader, and password reset cannot send an email
because nothing on this box can send one. Both of those are the same problem
from different ends: an account here is a username and a password, the password
is the only way back in, and there is no way back in.

Google already knows who somebody is and will say so. That is one fewer
password for a reader to invent, and — because Google asserts a VERIFIED email
address — it is also the first thing on this site that can prove an address
belongs to the person typing it.

The flow
--------
Authorization code, server side. The browser never sees the client secret and
never handles a token: it is redirected to Google, Google redirects it back
with a one-time code, and this server exchanges that code for an identity over
its own TLS connection.

The id_token's signature is NOT verified here, and that is correct rather than
lazy: it did not arrive from the browser, it came back on a direct HTTPS call
to accounts.google.com authenticated with the client secret. `aud` and `iss`
are still checked, because a token minted for a different application is a real
attack and costs one comparison to refuse.

Linking, and the rule that matters
----------------------------------
Three cases, and only one of them is delicate:

  * Already linked        -> sign that account in.
  * Signed in, linking    -> attach the Google id to the account in hand.
  * Neither               -> match on VERIFIED email, else create an account.

The third is where accounts get stolen if it is done carelessly. A Google
sign-in may only adopt an existing local account when Google says
`email_verified` AND the address matches exactly. Without that, anybody who
could create a Google account claiming an address could walk into the local
account holding it — which on this instance is the owner seat.

Configuration — both, or this is inert and says so:

    GOOGLE_CLIENT_ID       from console.cloud.google.com, OAuth 2.0 Client ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REDIRECT_URI    optional; defaults to <PUBLIC_BASE>/api/auth/google/callback

The redirect URI has to be registered with Google byte for byte.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from api.auth import (SESSION_COOKIE, COOKIE_SECURE, _create_session,
                      _set_session_cookie, get_current_user)
from db.session import get_db
from models.user import ROLE_READER, User

log = logging.getLogger(__name__)
router = APIRouter()

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://ficatlas.com").rstrip("/")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI",
                         f"{PUBLIC_BASE}/api/auth/google/callback")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# The state cookie. Short-lived, HttpOnly, and carries nothing but a random
# value and where to go afterwards — it is a CSRF guard, not a session.
STATE_COOKIE = "g_oauth"
STATE_TTL_MIN = 10


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _b64url(data: str) -> bytes:
    """Decode a JWT segment, which omits padding."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _claims(id_token: str) -> dict:
    """The payload of an id_token that came back from the token endpoint.

    See the module note on why the signature is not checked here. What IS
    checked is who the token was minted for and by.
    """
    try:
        payload = json.loads(_b64url(id_token.split(".")[1]))
    except Exception as e:
        raise HTTPException(400, "Google returned something unreadable") from e
    if payload.get("aud") != CLIENT_ID:
        raise HTTPException(400, "That sign-in was issued for a different app")
    if payload.get("iss") not in ISSUERS:
        raise HTTPException(400, "That sign-in did not come from Google")
    exp = payload.get("exp")
    if exp and datetime.now(timezone.utc).timestamp() > float(exp) + 60:
        raise HTTPException(400, "That sign-in has expired — try again")
    return payload


@router.get("/google/status")
def status():
    """Whether the button should exist at all. The UI asks before rendering it,
    so an unconfigured instance shows nothing rather than a control that
    fails."""
    return {"configured": configured()}


@router.get("/google/start")
def start(response: Response, request: Request,
          link: bool = False, next: str = "/",
          user: Optional[User] = Depends(get_current_user)):
    """Send the browser to Google.

    `link=true` attaches the result to the account already signed in; without a
    session that is a sign-in like any other.
    """
    if not configured():
        raise HTTPException(503, "Google sign-in is not configured on this site")

    nonce = secrets.token_urlsafe(24)
    # Only site-relative destinations, or this is an open redirect somebody can
    # point at their own domain from a link that looks like ours.
    dest = next if next.startswith("/") and not next.startswith("//") else "/"
    state = secrets.token_urlsafe(24)

    url = AUTH_URL + "?" + urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        # `select_account` so a shared machine does not silently sign in as
        # whoever used it last — the one thing worse than asking is guessing.
        "prompt": "select_account",
    })
    redirect = RedirectResponse(url, status_code=302)
    redirect.set_cookie(
        STATE_COOKIE,
        json.dumps({"s": state, "n": nonce, "next": dest,
                    "link": bool(link and user)}),
        max_age=STATE_TTL_MIN * 60, httponly=True, samesite="lax",
        secure=COOKIE_SECURE, path="/api/auth/google",
    )
    return redirect


@router.get("/google/callback")
def callback(request: Request, code: str = "", state: str = "",
             error: str = "",
             g_oauth: Optional[str] = Cookie(default=None, alias=STATE_COOKIE),
             db: Session = Depends(get_db),
             user: Optional[User] = Depends(get_current_user)):
    """Google sends the browser back here. Exchange, decide, sign in."""
    if not configured():
        raise HTTPException(503, "Google sign-in is not configured on this site")
    if error:
        return RedirectResponse(f"/login?sso={error}", status_code=302)
    if not code or not g_oauth:
        return RedirectResponse("/login?sso=expired", status_code=302)
    try:
        saved = json.loads(g_oauth)
    except Exception:
        return RedirectResponse("/login?sso=expired", status_code=302)
    # CSRF: the state that came back must be the state we issued.
    if not state or state != saved.get("s"):
        return RedirectResponse("/login?sso=state", status_code=302)

    try:
        with httpx.Client(timeout=20) as client:
            r = client.post(TOKEN_URL, data={
                "code": code, "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            })
        if r.status_code != 200:
            log.warning("google token exchange failed: %s %s",
                        r.status_code, r.text[:200])
            return RedirectResponse("/login?sso=exchange", status_code=302)
        tok = r.json()
    except Exception:
        log.warning("google token exchange errored", exc_info=True)
        return RedirectResponse("/login?sso=exchange", status_code=302)

    claims = _claims(tok.get("id_token") or "")
    if claims.get("nonce") and claims["nonce"] != saved.get("n"):
        return RedirectResponse("/login?sso=state", status_code=302)

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower() or None
    verified = bool(claims.get("email_verified"))
    if not sub:
        return RedirectResponse("/login?sso=exchange", status_code=302)

    account = _resolve_account(db, sub, email, verified,
                               signed_in=user,
                               linking=bool(saved.get("link")))

    token = _create_session(db, account,
                            user_agent=request.headers.get("user-agent"),
                            remember=True)
    dest = saved.get("next") or "/"
    out = RedirectResponse(dest, status_code=302)
    _set_session_cookie(out, token, remember=True)
    out.delete_cookie(STATE_COOKIE, path="/api/auth/google")
    return out


def _resolve_account(db: Session, sub: str, email: Optional[str],
                     verified: bool, signed_in: Optional[User],
                     linking: bool) -> User:
    """Which local account this Google identity is, creating one if need be."""
    existing = db.query(User).filter(User.google_sub == sub).first()
    if existing:
        return existing

    # Attaching to the account already in hand.
    if linking and signed_in is not None:
        signed_in.google_sub = sub
        if email and verified and not signed_in.email:
            signed_in.email = email
        db.commit()
        return signed_in

    # ADOPTING AN EXISTING ACCOUNT BY EMAIL, which is the delicate one. Only
    # when Google says the address is verified: without that, anybody able to
    # create a Google account claiming an address could walk into the local
    # account holding it, and on this instance that is the owner seat.
    if email and verified:
        by_email = db.query(User).filter(User.email == email).first()
        if by_email:
            by_email.google_sub = sub
            db.commit()
            log.info("google sign-in attached to existing account %s",
                     by_email.username)
            return by_email

    return _create_account(db, sub, email, verified)


def _create_account(db: Session, sub: str, email: Optional[str],
                    verified: bool) -> User:
    """A new reader, named from the address and made unique.

    No password is set. That is the point — the account has no password to
    forget and no password to reset — and `set-password` is how somebody adds
    one later if they want a way in that does not involve Google.
    """
    base = (email.split("@")[0] if email else "reader")
    base = "".join(c for c in base.lower() if c.isalnum() or c in "-_")[:20] or "reader"
    username = base
    for _ in range(50):
        if not db.query(User).filter(User.username == username).first():
            break
        username = f"{base}{secrets.randbelow(9000) + 1000}"
    else:  # pragma: no cover - 50 collisions on the same stem
        username = f"reader{secrets.token_hex(4)}"

    user = User(username=username,
                email=email if (email and verified) else None,
                password_hash=None,
                role=ROLE_READER)
    user.google_sub = sub
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("google sign-in created account %s", username)
    return user


@router.post("/google/unlink")
def unlink(db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Detach Google from this account.

    Refused when it is the only way in. An account created through Google has
    no password, and unlinking it would lock the person out of their own
    library with no way to recover it — which is precisely the failure this
    whole module exists to reduce.
    """
    if user is None:
        raise HTTPException(401, "Not authenticated")
    if not user.google_sub:
        return {"ok": True, "message": "This account is not linked to Google."}
    if not user.password_hash:
        raise HTTPException(
            400,
            "Set a password first — Google is currently the only way into "
            "this account, and unlinking it would lock you out.")
    user.google_sub = None
    db.commit()
    return {"ok": True, "message": "Google sign-in removed from this account."}
