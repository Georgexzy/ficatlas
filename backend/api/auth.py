"""Auth — signup/login/logout/me with bcrypt + httponly cookie sessions."""
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Response, Cookie, Form, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User, UserSession

router = APIRouter()

SESSION_DAYS    = 90
SESSION_COOKIE  = "sat"            # session-auth-token

# ── Brute-force protection ───────────────────────────────────────────────────
# Track failed login attempts per username. After too many fails in a window,
# lock that username briefly. In-memory is fine for single-process hobby scale.
import time as _time
_login_fails: dict[str, list[float]] = {}
_LOGIN_FAIL_WINDOW   = 300    # 5 min sliding window
_LOGIN_FAIL_MAX      = 8      # this many fails in the window → temporary lock
_LOGIN_LOCK_SECONDS  = 300    # lock duration


def _login_locked_for(username: str) -> float:
    """Return seconds remaining on a login lock for this username, else 0."""
    now = _time.time()
    fails = [t for t in _login_fails.get(username, []) if now - t < _LOGIN_FAIL_WINDOW]
    _login_fails[username] = fails
    if len(fails) >= _LOGIN_FAIL_MAX:
        # Locked until the oldest fail in the window ages out
        return max(0.0, _LOGIN_LOCK_SECONDS - (now - fails[0]))
    return 0.0


def _record_login_fail(username: str) -> None:
    _login_fails.setdefault(username, []).append(_time.time())


def _clear_login_fails(username: str) -> None:
    _login_fails.pop(username, None)


def hash_password(pw: str) -> str:
    # bcrypt has a 72-char limit — truncate quietly
    pw = pw[:72]
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(pw: str, h: str) -> bool:
    pw = pw[:72]
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), h.encode("utf-8"))
    except Exception:
        return False


def _new_token() -> str:
    return secrets.token_urlsafe(48)


def _token_hash(token: str) -> str:
    """What actually goes in the database.

    The session token is a bearer credential: whoever holds it *is* the user,
    with no second factor and no further check. Storing it verbatim meant the
    `user_sessions` table was a list of working credentials — so a database
    backup, a stray pg_dump, or read access to that one table was enough to
    resume every live session on the site. There is a `backups/` directory in
    this repo, which makes that a realistic path rather than a theoretical one.

    A hash removes the class entirely: the column becomes useless to anyone who
    reads it, while lookup stays a single indexed equality on the primary key.
    Plain SHA-256 rather than bcrypt is correct here, unlike for passwords —
    the input is 48 bytes from `secrets`, so there is no low-entropy guess to
    slow down, and this runs on every authenticated request.

    Hex digest is 64 chars and the column is String(80), so nothing migrates.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_session(db: Session, user: User, user_agent: str | None = None) -> str:
    token = _new_token()
    sess = UserSession(
        token=_token_hash(token), user_id=user.id,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
        last_used=datetime.utcnow(),
        user_agent=(user_agent or "")[:255],
    )
    db.add(sess)
    # Opportunistic cleanup: drop this user's expired sessions so the table
    # doesn't accumulate stale rows over time.
    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.expires_at < datetime.utcnow(),
    ).delete(synchronize_session=False)
    db.commit()
    return token


# Marking the session cookie Secure stops it ever being sent over plain HTTP,
# which is what we want the moment the site is reachable from the internet
# behind TLS (Cloudflare Tunnel terminates HTTPS, so it always is there). It
# cannot be hardcoded on, because over Tailscale the site is served plain http
# and a Secure cookie would simply never come back — logging in would appear to
# succeed and then every request would read as logged out.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE, value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


# How stale last_used may get before we bother writing it back. This field only
# drives the "active sessions" list, so minute-level precision is plenty.
_LAST_USED_REFRESH = timedelta(minutes=15)

# Sessions slide: once a session is within this much of expiring, using it pushes
# the expiry back out to a full SESSION_DAYS and re-sends the cookie with a fresh
# Max-Age. Without this, both the DB row and the browser cookie died a fixed 90
# days after login no matter how actively the account was used, and you were
# silently signed out. Refreshing only in the final third keeps this to roughly
# one extra write per month per device rather than one per request.
_SESSION_SLIDE_WHEN_UNDER = timedelta(days=SESSION_DAYS * 2 // 3)


def get_current_user(
    response: Response,
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not sat:
        return None
    # One round trip instead of two: the session and its user together.
    row = (
        db.query(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .filter(UserSession.token == _token_hash(sat))
        .first()
    )
    if not row:
        # Transitional: sessions created before tokens were hashed are stored
        # verbatim. Rather than invalidating everyone's login on deploy, accept
        # a legacy row once and rewrite it to its hash in place, so it is stored
        # correctly from here on. Every active session converts itself the next
        # time it is used, and the rest expire.
        #
        # Delete this once no legacy rows remain — they cannot outlive
        # SESSION_DAYS from the deploy that introduced hashing.
        legacy = (
            db.query(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .filter(UserSession.token == sat)
            .first()
        )
        if not legacy:
            return None
        s_old, user_old = legacy
        # token is the primary key, so this is a delete plus an insert.
        db.delete(s_old)
        db.flush()
        s_new = UserSession(
            token=_token_hash(sat), user_id=s_old.user_id,
            created_at=s_old.created_at, expires_at=s_old.expires_at,
            last_used=s_old.last_used, user_agent=s_old.user_agent,
            view_as=s_old.view_as,
        )
        db.add(s_new)
        db.commit()
        row = (s_new, user_old)
    s, user = row
    now = datetime.utcnow()
    if s.expires_at < now:
        db.delete(s); db.commit()
        return None

    dirty = False
    # Only write last_used when it has actually gone stale. Refreshing it on every
    # request turned each authenticated GET into an UPDATE + COMMIT, which meant a
    # row write (and WAL) per page view for no user-visible benefit.
    if s.last_used is None or (now - s.last_used) > _LAST_USED_REFRESH:
        s.last_used = now
        dirty = True

    if (s.expires_at - now) < _SESSION_SLIDE_WHEN_UNDER:
        s.expires_at = now + timedelta(days=SESSION_DAYS)
        dirty = True
        # The browser cookie carries its own Max-Age, so extending the DB row alone
        # would not keep the client signed in. Re-issue it with the same token.
        _set_session_cookie(response, sat)

    if dirty:
        db.commit()

    # Role preview. If this session asked to be seen as a lesser role, apply it
    # to the in-memory object only — never written back to the user row, so it
    # cannot outlive the session or leak into anyone else's view.
    #
    # min() of the two ranks, so this can only ever LOWER what you can do. An
    # attacker with a stolen session gains nothing from it, and a mistake in the
    # UI cannot escalate anybody.
    if s.view_as:
        from models.user import ROLE_RANK
        if ROLE_RANK.get(s.view_as, 99) < ROLE_RANK.get(user.role, 0):
            user.role = s.view_as
            user._previewing = True

    return user


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_role(user, db, needed: str):
    """Shared gate. Falls open only while the instance has no accounts at all.

    Accounts are optional here, so demanding one unconditionally would break a
    fresh install before you could sign up — you could not import anything.
    Once ANY account exists the instance is considered claimed and the gate
    closes.
    """
    from models.user import ROLE_OWNER
    if user is not None:
        if user.at_least(needed):
            return user
        raise HTTPException(
            403,
            f"This needs the {needed} role. Your account can search, read and "
            f"keep bookmarks, but not change the shared library.",
        )
    if db.query(User.id).first() is None:
        return None          # unclaimed instance — nothing to protect yet
    raise HTTPException(401, "Sign in to do this.")


def require_admin(
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Guard for endpoints that modify the shared library: uploads, URL imports,
    bulk scrapes, deleting hosted stories, dedup and cleanup batches.

    Every one of those was completely unauthenticated at one point, including
    DELETE /api/library/hosted/{id} — which deletes hosted full text.

    (An earlier version of this comment called the ~30k FicAlley works
    "irreplaceable, from a dead archive". That is wrong and worth correcting
    here because it was being used to justify decisions: FictionAlley moved its
    archive to AO3 through the OTW's Open Doors programme in 2018, and the
    works live at archiveofourown.org/collections/fictionalley. They are not
    lost, and Open Doors is the process through which their authors were given
    a say — so our copies are a duplicate of a consented migration, not a
    rescue.)

    Scraping in particular sits behind this because those requests leave from
    the HOST's IP: a reader who triggers them spends the operator's standing
    with AO3 and FF.net rather than their own.
    """
    from models.user import ROLE_ADMIN
    return _require_role(user, db, ROLE_ADMIN)


def require_owner(
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Guard for things that are hard or impossible to undo: destructive
    cleanup batches and changing other people's accounts."""
    from models.user import ROLE_OWNER
    return _require_role(user, db, ROLE_OWNER)


# Who may create an account, once the site is reachable from the internet:
#
#   open    anyone (the default, and what a public search engine wants)
#   invite  only with the shared code in SIGNUP_CODE
#   closed  nobody; accounts are made by hand
#
# The first account still becomes owner regardless (see init_db), so "closed" is
# safe to set before the owner exists only if that account is created directly.
SIGNUP_MODE = os.getenv("SIGNUP_MODE", "open").lower()
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "")


# ── endpoints ───────────────────────────────────────────────────────────────

@router.get("/signup-policy")
def signup_policy():
    """What the signup form should render. Public: it exposes no secret, only
    whether a code box is needed, and the frontend cannot guess that."""
    return {"mode": SIGNUP_MODE, "needs_code": SIGNUP_MODE == "invite"}


@router.post("/signup")
def signup(
    response: Response, request: Request,
    username: str = Form(...), password: str = Form(...),
    invite: str = Form(""),
    db: Session = Depends(get_db),
):
    if SIGNUP_MODE == "closed":
        raise HTTPException(403, "This site is not accepting new accounts")
    if SIGNUP_MODE == "invite":
        # compare_digest so a wrong code cannot be recovered a character at a
        # time from response timing.
        if not SIGNUP_CODE or not secrets.compare_digest(invite.strip(), SIGNUP_CODE):
            raise HTTPException(403, "That invite code is not valid")

    username = username.strip().lower()
    if not (3 <= len(username) <= 30):
        raise HTTPException(400, "Username must be 3–30 characters")
    if not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "Username can only contain letters, numbers, _ and -")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "Username already taken")
    user = User(username=username, password_hash=hash_password(password),
                last_login=datetime.utcnow())
    db.add(user); db.commit(); db.refresh(user)
    token = _create_session(db, user, request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    return {"username": user.username, "id": str(user.id)}


@router.post("/login")
def login(
    response: Response, request: Request,
    username: str = Form(...), password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip().lower()
    locked = _login_locked_for(username)
    if locked > 0:
        raise HTTPException(
            429,
            f"Too many failed attempts. Try again in {int(locked)} seconds."
        )
    user = db.query(User).filter(User.username == username).first()
    if not user or not check_password(password, user.password_hash):
        _record_login_fail(username)
        raise HTTPException(401, "Invalid username or password")
    _clear_login_fails(username)
    user.last_login = datetime.utcnow()
    token = _create_session(db, user, request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    db.commit()
    return {"username": user.username, "id": str(user.id)}


@router.post("/logout")
def logout(
    response: Response,
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
):
    if sat:
        # Both forms, so a session that has not yet been converted to its hash
        # is still genuinely destroyed. A logout that silently leaves the
        # session alive is the worst possible bug in this function.
        db.query(UserSession).filter(
            UserSession.token.in_([_token_hash(sat), sat])).delete(
                synchronize_session=False)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: Optional[User] = Depends(get_current_user)):
    if not user:
        return {"user": None}
    from models.user import ROLE_ADMIN, ROLE_OWNER
    return {"user": {
        "username": user.username,
        "id": str(user.id),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        # Needed so the account page can show whether a reset could ever reach
        # this person by email, rather than leaving them to find out at the
        # moment they are locked out.
        "email": user.email,
        # The UI needs these to decide what to SHOW, not just what to allow.
        # Rendering an Import tab that 403s on every button is worse than not
        # rendering it: the reader learns the app is broken rather than that
        # the feature is not theirs.
        "role": user.role,
        "can_import": user.at_least(ROLE_ADMIN),
        "can_manage": user.at_least(ROLE_OWNER),
        # So the UI can show an unmissable banner. Forgetting you are in preview
        # and concluding a feature is broken is the obvious failure mode.
        "previewing": bool(getattr(user, "_previewing", False)),
    }}


@router.post("/change-password")
def change_password(
    response: Response, request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Change password. Verifies the current password, updates the hash, and
    invalidates all OTHER sessions (keeping the current one) so a leaked old
    session can't persist after a password change."""
    if not check_password(current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect")
    if len(new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    user.password_hash = hash_password(new_password)
    # Drop every session except the one making this request
    q = db.query(UserSession).filter(UserSession.user_id == user.id)
    if sat:
        # notin_ both forms: matching only the hash would sign the caller out of
        # the very device they are changing their password on, if that session
        # happened not to have been converted yet.
        q = q.filter(UserSession.token.notin_([_token_hash(sat), sat]))
    q.delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "message": "Password changed. Other devices were signed out."}


@router.post("/view-as")
def set_view_as(role: str = Form(""), sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
                      db: Session = Depends(get_db)):
    """Preview the site as a lesser role, or clear the preview with an empty value.

    Not impersonation, and the difference matters. Impersonation adopts another
    person's account: their bookmarks, their reading history, their settings,
    with no read-only mode — which is why the standard advice is to treat it as
    a last resort. None of that is required to answer "what does a reader
    actually see?", which is the real question when checking that a role gate
    works.

    So this downgrades your OWN role for your OWN session. No other account is
    touched, no private data is exposed, and there is nothing to audit beyond
    your own session because nobody else is involved.
    """
    from models.user import ROLE_RANK, ROLE_READER, ROLE_ADMIN, ROLE_OWNER

    sess = db.query(UserSession).filter(
        UserSession.token.in_([_token_hash(sat), sat])).first() if sat else None
    if not sess:
        raise HTTPException(401, "Not authenticated")
    user = db.query(User).filter(User.id == sess.user_id).first()
    if not user:
        raise HTTPException(401, "Not authenticated")

    role = (role or "").strip().lower()
    if not role:
        sess.view_as = None
        db.commit()
        return {"view_as": None, "role": user.role}

    if role not in (ROLE_READER, ROLE_ADMIN, ROLE_OWNER):
        raise HTTPException(400, "Unknown role")
    # Downgrade only. Enforced here as well as at read time, so neither path
    # alone is load-bearing.
    if ROLE_RANK.get(role, 99) >= ROLE_RANK.get(user.role, 0):
        raise HTTPException(400, "Preview can only show a role with fewer permissions than your own")

    sess.view_as = role
    db.commit()
    return {"view_as": role, "real_role": user.role}


@router.post("/email")
def set_email(password: str = Form(...), email: str = Form(""),
                    user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Add, change or clear the account's contact address.

    Requires the current password. Without that, anyone who got hold of a
    session could point the address at themselves and then use the reset flow to
    take the account permanently — turning a stolen session into a stolen
    account.

    An empty value clears it, which has to stay possible: the address is
    optional, and someone who added one should be able to take it back.
    """
    if not check_password(password, user.password_hash):
        raise HTTPException(403, "That password is not correct")

    email = email.strip().lower()
    if email:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(400, "That does not look like an email address")
        clash = (db.query(User)
                 .filter(func.lower(User.email) == email, User.id != user.id)
                 .first())
        if clash:
            raise HTTPException(400, "Another account already uses that address")
        user.email = email
    else:
        user.email = None
    db.commit()
    return {"ok": True, "email": user.email}


@router.get("/sessions")
def list_sessions(
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """List active sessions for this account so the user can see where they're
    signed in and revoke other devices."""
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .order_by(UserSession.last_used.desc())
        .all()
    )
    return {
        "sessions": [
            {
                "current": s.token == sat,
                "user_agent": s.user_agent or "Unknown device",
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_used": s.last_used.isoformat() if s.last_used else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                # Never expose the token itself; use a short fingerprint for revoke
                "fp": s.token[:12],
            }
            for s in rows
        ]
    }


@router.post("/logout-all")
def logout_all(
    response: Response,
    keep_current: bool = Form(True),
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Sign out everywhere. If keep_current is true, the requesting device stays
    signed in; otherwise the cookie is cleared too."""
    q = db.query(UserSession).filter(UserSession.user_id == user.id)
    if keep_current and sat:
        q = q.filter(UserSession.token.notin_([_token_hash(sat), sat]))
    q.delete(synchronize_session=False)
    db.commit()
    if not keep_current:
        response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/delete-account")
def delete_account(
    response: Response,
    password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Permanently delete the account and all associated data (sessions + userdata
    cascade via FK ON DELETE CASCADE). Requires the password as confirmation."""
    if not check_password(password, user.password_hash):
        raise HTTPException(401, "Password is incorrect")
    # Explicitly remove children first so deletion works whether or not the ORM
    # relationship cascade is configured (the DB FK is ON DELETE CASCADE, but the
    # ORM session may not know that).
    from models.user import UserData, UserSession
    db.query(UserData).filter(UserData.user_id == user.id).delete(synchronize_session=False)
    db.query(UserSession).filter(UserSession.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "message": "Account deleted."}

# ── Account management (owner only) ─────────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _owner=Depends(require_owner)):
    """Every account and its role. Owner-only: it exposes who exists."""
    rows = db.query(User).order_by(User.created_at.asc()).all()
    return {"users": [{
        "id": str(u.id), "username": u.username, "role": u.role,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
    } for u in rows]}


@router.post("/users/{user_id}/role")
def set_user_role(
    user_id: str,
    role: str = Form(...),
    db: Session = Depends(get_db),
    owner: Optional[User] = Depends(require_owner),
):
    """Change an account's role."""
    from models.user import ROLE_ADMIN, ROLE_OWNER, ROLE_RANK
    role = (role or "").strip().lower()
    if role not in ROLE_RANK:
        raise HTTPException(400, f"Unknown role {role!r}.")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "No such account.")

    # An owner must not demote themselves out of the only owner seat — that
    # locks the instance permanently, with no way back short of SQL.
    if owner is not None and str(target.id) == str(owner.id) and role != ROLE_OWNER:
        others = (db.query(User.id)
                  .filter(User.role == ROLE_OWNER, User.id != target.id).first())
        if not others:
            raise HTTPException(
                400, "You are the only owner. Promote someone else first.")

    target.role = role
    db.commit()
    return {"ok": True, "username": target.username, "role": target.role}
