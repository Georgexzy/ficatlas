"""Auth — signup/login/logout/me with bcrypt + httponly cookie sessions."""
import secrets
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Response, Cookie, Form, Request
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


def _create_session(db: Session, user: User, user_agent: str | None = None) -> str:
    token = _new_token()
    sess = UserSession(
        token=token, user_id=user.id,
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


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE, value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax",
        secure=False,           # site is served plain http over Tailscale; flip when behind TLS
        path="/",
    )


def get_current_user(
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not sat:
        return None
    s = db.query(UserSession).filter(UserSession.token == sat).first()
    if not s:
        return None
    if s.expires_at < datetime.utcnow():
        db.delete(s); db.commit()
        return None
    # Refresh last_used (cheap)
    s.last_used = datetime.utcnow()
    db.commit()
    return db.query(User).filter(User.id == s.user_id).first()


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


# ── endpoints ───────────────────────────────────────────────────────────────

@router.post("/signup")
async def signup(
    response: Response, request: Request,
    username: str = Form(...), password: str = Form(...),
    db: Session = Depends(get_db),
):
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
async def login(
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
async def logout(
    response: Response,
    sat: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
):
    if sat:
        db.query(UserSession).filter(UserSession.token == sat).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: Optional[User] = Depends(get_current_user)):
    if not user:
        return {"user": None}
    return {"user": {
        "username": user.username,
        "id": str(user.id),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }}


@router.post("/change-password")
async def change_password(
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
        q = q.filter(UserSession.token != sat)
    q.delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "message": "Password changed. Other devices were signed out."}


@router.get("/sessions")
async def list_sessions(
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
async def logout_all(
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
        q = q.filter(UserSession.token != sat)
    q.delete(synchronize_session=False)
    db.commit()
    if not keep_current:
        response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/delete-account")
async def delete_account(
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
