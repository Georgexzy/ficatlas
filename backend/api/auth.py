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
    user = db.query(User).filter(User.username == username).first()
    if not user or not check_password(password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
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
    return {"user": {"username": user.username, "id": str(user.id)}}
