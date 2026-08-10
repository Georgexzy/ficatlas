"""
Password reset, for a site that cannot rely on sending email.
=============================================================

Until now a user who forgot their password was locked out permanently and the
only fix was editing the database by hand. That is tolerable with one account
and untenable with strangers — it is the single commonest support request any
site with logins receives.

The awkward part is delivery. FicAtlas runs on a home connection behind a
tunnel, and mail from a residential IP is routinely dropped or binned by the big
providers. Building this as though email always arrives would produce a reset
flow that silently fails for most people, which is worse than not having one.

So delivery is pluggable and the flow works without it:

  SMTP configured    the code is emailed, and the user never sees an admin.
  no SMTP            the request still creates a code; an admin reads it out of
                     the pending list and passes it on by whatever channel they
                     and the user actually share (Discord, Tumblr, wherever the
                     account came from). Slower, but it works, and it is honest
                     about who is vouching for whom.

Security decisions worth stating:

  * Only the SHA-256 of the token is stored. This table appears in every
    backup, and a plaintext reset token in a backup is a live key to an account.
  * The request endpoint always reports success, whether or not the account
    exists. Otherwise it becomes a way to enumerate usernames.
  * Tokens are single-use and expire in an hour.
  * Completing a reset destroys every existing session for that user. If the
    reason for resetting was that somebody else had the password, leaving their
    session alive would defeat the whole exercise.
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User, UserSession
from api.auth import hash_password, require_admin

log = logging.getLogger(__name__)
router = APIRouter()

TOKEN_TTL_MIN = int(os.getenv("RESET_TTL_MIN", "60"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
# No default sender: the old one was no-reply@ficatlas.app, a domain nobody
# owns, so any deployment that enabled SMTP without setting this would have
# sent from an address that cannot receive bounces and would likely be
# rejected outright. Mail is off unless a deployment configures both.
SMTP_FROM = os.getenv("SMTP_FROM", "")
SITE_URL = os.getenv("SITE_URL", "")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _send_email(to: str, code: str) -> bool:
    """Best-effort delivery. Returns whether it actually went out."""
    # Both, not just the host. SMTP_FROM no longer defaults to a domain nobody
    # owns, so a deployment that sets a host and forgets the sender would
    # otherwise build a message with an empty From — rejected by most servers,
    # and the failure would look like the reset flow being broken rather than
    # unconfigured.
    if not SMTP_HOST or not SMTP_FROM:
        return False
    link = f"{SITE_URL}/reset?code={code}" if SITE_URL else None
    body = (
        "Someone asked to reset the password on your FicAtlas account.\n\n"
        f"Your reset code is:\n\n    {code}\n\n"
        + (f"Or open:\n\n    {link}\n\n" if link else "")
        + f"It stops working in {TOKEN_TTL_MIN} minutes and can only be used once.\n"
        "If this was not you, ignore this message — nothing has changed.\n"
    )
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = "Reset your FicAtlas password"
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:
        # Never surface this to the caller: whether mail worked must not become
        # a signal about whether the account exists.
        log.warning(f"reset email failed: {type(e).__name__}: {e}")
        return False


@router.post("/forgot")
def forgot_password(username: str = Form(...), db: Session = Depends(get_db)):
    """Start a reset. Always reports success."""
    user = db.query(User).filter(User.username == username.strip().lower()).first()

    if user:
        token = secrets.token_urlsafe(24)
        db.execute(sql_text("""
            INSERT INTO password_resets (user_id, token_hash, expires_at)
            VALUES (:uid, :th, now() + (:mins || ' minutes')::interval)
        """), {"uid": str(user.id), "th": _hash(token), "mins": TOKEN_TTL_MIN})
        db.commit()

        delivered = bool(user.email) and _send_email(user.email, token)
        if delivered:
            db.execute(sql_text("UPDATE password_resets SET delivered = TRUE WHERE token_hash = :th"),
                       {"th": _hash(token)})
            db.commit()
        else:
            # Deliberately logged so the operator can retrieve it. This is the
            # no-email path, and the alternative is the user staying locked out.
            log.info(f"password reset for {user.username}: no mail sent, "
                     f"code must be passed on by hand (see /api/auth/reset-requests)")

    # Same answer either way — see module docstring.
    return {
        "ok": True,
        "message": ("If that account exists, a reset code has been created. "
                    "Check your email, or contact whoever runs this site if you "
                    "did not add an address."),
    }


@router.post("/reset")
def reset_password(code: str = Form(...), new_password: str = Form(...),
                         db: Session = Depends(get_db)):
    if len(new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    row = db.execute(sql_text("""
        SELECT id, user_id FROM password_resets
        WHERE token_hash = :th AND used_at IS NULL AND expires_at > now()
    """), {"th": _hash(code.strip())}).first()
    if not row:
        raise HTTPException(400, "That reset code is not valid, or it has expired.")

    user = db.query(User).filter(User.id == row[1]).first()
    if not user:
        raise HTTPException(400, "That reset code is not valid.")

    user.password_hash = hash_password(new_password)
    db.execute(sql_text("UPDATE password_resets SET used_at = now() WHERE id = :i"),
               {"i": str(row[0])})
    # Every other outstanding code for this user dies too, so a second stolen
    # code cannot be used after a legitimate reset.
    db.execute(sql_text("""
        UPDATE password_resets SET used_at = now()
        WHERE user_id = :u AND used_at IS NULL
    """), {"u": str(user.id)})
    # And every session: if the password leaked, an attacker's live session
    # would otherwise survive the reset that was meant to evict them.
    db.query(UserSession).filter(UserSession.user_id == user.id).delete(
        synchronize_session=False)
    db.commit()

    log.info(f"password reset completed for {user.username}; all sessions ended")
    return {"ok": True, "message": "Password changed. You can sign in now."}


@router.post("/admin/issue-reset")
def admin_issue_reset(username: str = Form(...), db: Session = Depends(get_db),
                            admin: User = Depends(require_admin)):
    """Mint a reset code for a user and return it once, to the admin.

    This is the other half of the no-email path, and it was missing: /forgot
    creates a code but deliberately never reveals it, and /reset-requests shows
    only that a request exists. Without this the whole no-SMTP flow was a dead
    end — an admin could see somebody was locked out and had no way to help.

    Deliberately a separate, explicit admin action rather than exposing codes
    from the user-facing endpoints. The distinction matters: a user asking to
    reset must never produce a readable token anywhere, or the request endpoint
    becomes an account-takeover tool. An admin choosing to vouch for a specific
    person is a decision someone made, and it is logged as one.

    The admin is expected to pass the code back through whatever channel they
    already share with that user. Verifying identity is a human judgement and
    this endpoint does not pretend otherwise.
    """
    user = db.query(User).filter(User.username == username.strip().lower()).first()
    if not user:
        raise HTTPException(404, "No such account")

    token = secrets.token_urlsafe(24)
    # Any code already outstanding for this user is retired, so exactly one is
    # live at a time and handing out a new one invalidates an older leak.
    db.execute(sql_text("""
        UPDATE password_resets SET used_at = now()
        WHERE user_id = :u AND used_at IS NULL
    """), {"u": str(user.id)})
    db.execute(sql_text("""
        INSERT INTO password_resets (user_id, token_hash, expires_at, delivered)
        VALUES (:uid, :th, now() + (:mins || ' minutes')::interval, FALSE)
    """), {"uid": str(user.id), "th": _hash(token), "mins": TOKEN_TTL_MIN})
    db.commit()

    log.info(f"admin {admin.username} issued a reset code for {user.username}")
    return {
        "username": user.username,
        "code": token,
        "expires_in_minutes": TOKEN_TTL_MIN,
        "note": ("Give this to the account holder. It works once and is not "
                 "stored anywhere readable, so it cannot be shown again."),
    }


@router.get("/reset-requests")
def list_reset_requests(db: Session = Depends(get_db),
                              _admin: User = Depends(require_admin)):
    """Open reset requests, for the no-email case.

    Shows WHETHER a code is outstanding and for whom — never the code itself,
    which exists only in the log and the user's inbox. An admin endpoint that
    handed out working reset tokens would turn one compromised admin session
    into every account on the site.
    """
    rows = db.execute(sql_text("""
        SELECT u.username, u.email, r.created_at, r.expires_at, r.delivered
        FROM password_resets r JOIN users u ON u.id = r.user_id
        WHERE r.used_at IS NULL AND r.expires_at > now()
        ORDER BY r.created_at DESC LIMIT 50
    """)).fetchall()
    return [{
        "username": r[0], "email": r[1],
        "created_at": r[2].isoformat(), "expires_at": r[3].isoformat(),
        "emailed": r[4],
    } for r in rows]
