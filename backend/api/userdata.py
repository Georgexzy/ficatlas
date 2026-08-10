"""Per-user JSON storage with smart merge (bookmarks, progress, recents, settings).

The key robustness improvement over naive last-write-wins: the /merge endpoint
combines client state with server state per-key using type-aware rules, so using
two devices never silently drops data.
"""
from typing import Any
import json
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User, UserData
from api.auth import require_user

router = APIRouter()

ALLOWED_KEYS = {"bookmarks", "progress", "recents", "settings", "explicit"}
MAX_BYTES    = 2 * 1024 * 1024   # 2 MB per key


def _merge_value(key: str, client: Any, server: Any) -> Any:
    """Type-aware merge of a client value and the existing server value.

    - bookmarks: array of story objects/ids → union, dedup by id (or by value)
    - recents:   array → union preserving recency, cap at 50
    - progress:  dict keyed by story id → per-story keep the most-recently-updated
    - settings:  dict of unrelated preferences → merged per key, client winning
                 the keys it set, so one device never wipes another's choices
    - explicit:  scalar → client wins (it's the active device's choice)
    """
    if server is None:
        return client
    if client is None:
        return server

    if key == "bookmarks":
        return _merge_id_array(client, server)

    if key == "recents":
        # recents are usually arrays of strings or {q, at} objects; union, cap 50
        merged = _merge_id_array(client, server, cap=50)
        return merged

    if key == "progress":
        # dict: { storyId: { chapter, scrollPct, at, positions: {chapterNo: pct} } }
        if not isinstance(client, dict) or not isinstance(server, dict):
            return client
        out = dict(server)
        for sid, c_entry in client.items():
            s_entry = server.get(sid)
            if s_entry is None:
                out[sid] = c_entry
            else:
                # Keep whichever was updated most recently (by 'at' ISO timestamp)
                c_at = (c_entry or {}).get("at", "") if isinstance(c_entry, dict) else ""
                s_at = (s_entry or {}).get("at", "") if isinstance(s_entry, dict) else ""
                winner, loser = ((c_entry, s_entry) if c_at >= s_at
                                 else (s_entry, c_entry))
                out[sid] = winner

                # `positions` is a per-chapter map and must be unioned, not
                # replaced with the winner's copy.
                #
                # Whole-entry last-write-wins was right when progress was a
                # single scrollPct — there was one number and the newer one was
                # the answer. It is wrong now the entry carries a position per
                # chapter: reading chapter 7 on a phone would discard the
                # position in chapter 3 recorded on a laptop, so going back a
                # chapter lost your place on the device that had never moved.
                #
                # The winner still owns the scalar fields (which chapter you are
                # on, and when) — those genuinely are last-write-wins. Only the
                # map is combined, with the winner taking any chapter both hold.
                if isinstance(winner, dict) and isinstance(loser, dict):
                    w_pos, l_pos = winner.get("positions"), loser.get("positions")
                    if isinstance(w_pos, dict) or isinstance(l_pos, dict):
                        merged_pos = dict(l_pos if isinstance(l_pos, dict) else {})
                        merged_pos.update(w_pos if isinstance(w_pos, dict) else {})
                        out[sid] = {**winner, "positions": merged_pos}
        return out

    if key == "settings" and isinstance(client, dict) and isinstance(server, dict):
        # Per-KEY merge, not whole-object replace. Settings is one JSON blob holding
        # unrelated preferences (reader font, column width, default sites, poll
        # options...). Replacing the whole object meant that a phone which had never
        # seen a preference changed on the laptop would wipe it on its next sync —
        # silent cross-device data loss. Merging per key keeps both devices' choices,
        # with the active device still winning any key it actually set.
        return {**server, **client}

    # explicit, and anything else scalar: client (active device) wins
    return client


def _merge_id_array(client: Any, server: Any, cap: int | None = None) -> list:
    """Union two arrays, deduping. Items may be strings or dicts with an 'id'/'url'.
    Client items take precedence (appear first) so recency/edits from the active
    device win, but nothing from the server is lost."""
    if not isinstance(client, list): client = []
    if not isinstance(server, list): server = []

    def key_of(item):
        if isinstance(item, dict):
            return item.get("id") or item.get("url") or json.dumps(item, sort_keys=True)
        return item

    seen = set()
    out = []
    for item in [*client, *server]:
        k = key_of(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    if cap is not None:
        out = out[:cap]
    return out


def _check_size(value: Any) -> None:
    size = len(json.dumps(value))
    if size > MAX_BYTES:
        raise HTTPException(413, f"Payload too large ({size} bytes; max {MAX_BYTES})")


@router.get("")
def get_all(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.query(UserData).filter(UserData.user_id == user.id).all()
    return {r.key: r.value for r in rows}


@router.get("/{key}")
def get_one(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    row = db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).first()
    return {"value": row.value if row else None}


@router.put("/{key}")
def put_one(
    key: str,
    value: Any = Body(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    _check_size(value)
    row = db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).first()
    if row:
        row.value = value
    else:
        db.add(UserData(user_id=user.id, key=key, value=value))
    db.commit()
    return {"ok": True}


@router.post("/merge")
def merge_all(
    payload: dict = Body(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Merge a full client snapshot with server state and return the merged result.

    Body: { "bookmarks": [...], "progress": {...}, "recents": [...], ... }
    For each provided key we merge with what's on the server (type-aware), persist
    the merged value, and return the complete merged set so the client can adopt it.

    This is the robust sync primitive: call it on login and periodically, and no
    device ever clobbers another's data.
    """
    existing = {
        r.key: r.value
        for r in db.query(UserData).filter(UserData.user_id == user.id).all()
    }
    result: dict[str, Any] = dict(existing)

    for key, client_val in payload.items():
        if key not in ALLOWED_KEYS:
            continue
        merged = _merge_value(key, client_val, existing.get(key))
        _check_size(merged)
        result[key] = merged
        row = db.query(UserData).filter(
            UserData.user_id == user.id, UserData.key == key
        ).first()
        if row:
            row.value = merged
        else:
            db.add(UserData(user_id=user.id, key=key, value=merged))

    db.commit()
    return result


@router.delete("/{key}")
def delete_one(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key '{key}'")
    db.query(UserData).filter(UserData.user_id == user.id, UserData.key == key).delete()
    db.commit()
    return {"ok": True}
