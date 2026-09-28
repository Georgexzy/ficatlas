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
from sqlalchemy import and_, func, or_, text as sql_text
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User, UserSession
from api.auth import check_password, hash_password, require_admin

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
# Where this site lives, for the clickable link in the reset mail.
#
# Falls back to PUBLIC_BASE_URL, which google_sso.py already reads and which is
# set in .env and both compose files. SITE_URL was set NOWHERE — so every reset
# mail would have gone out with the bare code and no link, which is the
# difference between "paste this into the site" and "click here" at the moment
# somebody is already locked out and frustrated.
#
# Not a new variable, deliberately. A third spelling of the same fact
# (PUBLIC_SITE_URL is a fourth, on the frontend) is how these drift apart, and
# this repo has spent real time on exactly that kind of duplication.
SITE_URL = os.getenv("SITE_URL", "") or os.getenv("PUBLIC_BASE_URL", "")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _send_email(to: str, code: str, username: str) -> bool:
    """Best-effort delivery. Returns whether it actually went out.

    `username` is named in the body deliberately. One address can hold several
    accounts here, and "your FicAtlas account" leaves the reader guessing which
    — while naming it also lets somebody who did NOT request this see at a
    glance whether it concerns an account they recognise.
    """
    # Both, not just the host. SMTP_FROM no longer defaults to a domain nobody
    # owns, so a deployment that sets a host and forgets the sender would
    # otherwise build a message with an empty From — rejected by most servers,
    # and the failure would look like the reset flow being broken rather than
    # unconfigured.
    if not SMTP_HOST or not SMTP_FROM:
        return False
    # THE CODE GOES IN THE FRAGMENT, NOT THE QUERY STRING.
    #
    # `?code=` puts a live single-use token everywhere a URL is recorded. Checked
    # rather than assumed: nginx logs the full request line with its query string
    # (`"GET /takedown?url=%2Fstory%2F… HTTP/1.1"` is in today's access log), and
    # this site loads Google Fonts, so the whole URL would also travel to a third
    # party in the Referer header of the first stylesheet request.
    #
    # A fragment is never sent to a server by anything: not in the request line,
    # not in Referer, not to the CDN. The page reads it from location.hash
    # instead — the same reason OAuth's implicit flow returned tokens that way.
    link = f"{SITE_URL}/reset#code={code}" if SITE_URL else None
    # THE COPY. Written against how this is done elsewhere rather than from
    # taste, after two drafts the operator rejected. The elements every guide
    # agrees on, and which the earlier versions were missing pieces of:
    #
    #   * say WHO it is for at the top — one address can hold several accounts
    #   * ONE call to action, with nothing competing for the click
    #   * the copyable fallback, for when the link is mangled or unclickable
    #   * expiry stated plainly
    #   * a reassurance line for somebody who did not ask
    #   * a way to reach a person
    #
    # What is deliberately NOT here: "Someone asked to reset the password on
    # your account". It describes the REQUEST rather than telling the reader what
    # to do, and "someone" reads as a stranger in a message that is almost always
    # the reader themselves, seconds after clicking the button.
    #
    # Plain text, no HTML part: a second body to keep in step, for a message
    # whose every claim is one sentence. Indented blocks instead — every client
    # renders those, and they make the link and the code selectable as a unit.
    # Wrapped under 72 so nothing re-flows ragged.
    bare = (SITE_URL or "https://ficatlas.com").replace("https://", "")
    body = (
        f"Hi {username},\n"
        "\n"
        "Use the link below to choose a new password for your FicAtlas\n"
        "account.\n"
        "\n"
        + (f"    {link}\n"
           "\n"
           f"The link expires in {TOKEN_TTL_MIN} minutes and can be used once.\n"
           "\n"
           f"If it does not open, go to {bare}/reset and enter this code:\n"
           "\n"
           f"    {code}\n"
           if link else
           f"Go to {bare}/reset and enter this code:\n"
           "\n"
           f"    {code}\n"
           "\n"
           f"It expires in {TOKEN_TTL_MIN} minutes and can be used once.\n")
        + "\n"
        "If you did not request this, you can ignore this email. Your\n"
        "password has not been changed, and the link cannot be used by\n"
        "anyone without access to this mailbox.\n"
        "\n"
        "Need help? Write to help@ficatlas.com.\n"
        "\n"
        "-- \n"
        "FicAtlas\n"
        "Search AO3, FanFiction.net and FictionAlley at once\n"
        f"{SITE_URL or 'https://ficatlas.com'}\n"
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
    """Start a reset. Always reports success.

    The field takes a USERNAME OR AN EMAIL ADDRESS. It is still named
    `username` because that is what the form has always posted and renaming it
    would break any client mid-deploy — what changed is what it accepts.

    Somebody who has forgotten their password has usually also forgotten which
    of the two they signed up with, and this site makes that worse than most:
    the username is chosen, the address is optional, and sign-in uses the
    username — so the identifier a reader actually remembers is very often the
    one the old form refused. Asking for the one thing they cannot produce is
    how a reset form becomes a support request.

    Matched case-insensitively on both. Usernames and addresses are both stored
    lower-cased, so lower() on the column compares like with like rather than
    papering over a mismatch.
    """
    ident = (username or "").strip().lower()
    user = (db.query(User)
              .filter(or_(func.lower(User.username) == ident,
                          and_(User.email.isnot(None),
                               func.lower(User.email) == ident)))
              .first())

    if user:
        token = secrets.token_urlsafe(24)
        db.execute(sql_text("""
            INSERT INTO password_resets (user_id, token_hash, expires_at)
            VALUES (:uid, :th, now() + (:mins || ' minutes')::interval)
        """), {"uid": str(user.id), "th": _hash(token), "mins": TOKEN_TTL_MIN})
        db.commit()

        delivered = bool(user.email) and _send_email(user.email, token, user.username)
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

    # THE SAME PASSWORD IS NOT A RESET.
    #
    # Checked AFTER the code is validated, never before: answering "that is your
    # current password" to an unvalidated code would make this endpoint an
    # oracle for testing passwords against an account somebody does not hold.
    #
    # Refused rather than quietly accepted, because of what a reset MEANS here.
    # It ends every session for the account, on the reasoning that a password
    # being reset may have leaked. Setting the same one back signs the reader
    # out of everything, burns the code, and leaves the leaked password working
    # — the worst of both. The message says what to do instead, not only what
    # went wrong.
    if check_password(new_password, user.password_hash):
        raise HTTPException(
            400,
            "That is already your password. Choose a different one - a reset "
            "signs you out everywhere, and reusing the old password would "
            "leave it working.")

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
