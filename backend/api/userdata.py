"""Per-user JSON storage (bookmarks, progress, recents, settings)."""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User, UserData
from api.auth import require_user

router = APIRouter()

ALLOWED_KEYS = {"bookmarks", "progress", "recents", "settings", "explicit"}
MAX_BYTES    = 2 * 1024 * 1024   # 2 MB per key


@router.get("")
async def get_all(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.query(UserData).filter(UserData.user_id == user.id).all()
    return {r.key: r.value for r in rows}


@router.get("/{key}")
async def get_one(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    row = db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).first()
    return {"value": row.value if row else None}


@router.put("/{key}")
async def put_one(
    key: str,
    value: Any = Body(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    # Reject absurdly large payloads
    import json
    size = len(json.dumps(value))
    if size > MAX_BYTES:
        raise HTTPException(413, f"Payload too large ({size} bytes; max {MAX_BYTES})")
    row = db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).first()
    if row:
        row.value = value
    else:
        db.add(UserData(user_id=user.id, key=key, value=value))
    db.commit()
    return {"ok": True}


@router.delete("/{key}")
async def delete_one(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).delete()
    db.commit()
    return {"ok": True}
