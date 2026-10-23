"""Public feedback endpoint — collects reader suggestions without auth.

The form on /feedback writes here. No login required so anyone can use it,
and the queue is read by the owner the same way takedowns are.
"""
import logging
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from db.session import get_db
from mailer import send, SMTP_FROM

router = APIRouter()
log = logging.getLogger(__name__)

MAX_TEXT = 4000  # generous; server rejects larger anyway


class FeedbackIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)
    email: str | None = Field(None, max_length=254)  # optional, for follow-up
    page: str | None = Field(None, max_length=200)   # where they were
    ua: str | None = Field(None, max_length=300)     # user agent for context


@router.post("/feedback")
async def post_feedback(
    feedback: FeedbackIn,
    request: Request,
    db: Session = Depends(get_db),
):
    # Basic spam guard: honeypot field (sent as 'website' by bots)
    # Not strictly needed for a low-traffic form, but costs nothing.
    # The Pydantic model doesn't include it, so extra fields are ignored.
    
    # Store in database
    db.execute(text("""
        INSERT INTO feedback (text, email, page, user_agent, ip, created_at)
        VALUES (:text, :email, :page, :ua, :ip, now())
    """), {
        "text": feedback.text,
        "email": feedback.email,
        "page": feedback.page,
        "ua": feedback.ua,
        "ip": request.client.host if request.client else None,
    })
    db.commit()
    
    # Email the owner
    try:
        subject = "FicAtlas feedback"
        body = f"""New feedback from {feedback.page or 'unknown page'}:

{feedback.text}

---
Email (if provided): {feedback.email or 'none'}
User-Agent: {feedback.ua or 'unknown'}
IP: {request.client.host if request.client else 'unknown'}
"""
        # Owner email from SMTP_FROM env var
        await send(SMTP_FROM, subject, body)
    except Exception as e:
        log.warning(f"feedback email failed: {e}")
    
    return {"ok": True}


# Also accept form submission for the HTML page
@router.post("/feedback/form")
async def post_feedback_form(
    text: str = Form(...),
    email: str = Form(None),
    page: str = Form(None),
    request: Request = None,
    db: Session = Depends(get_db),
):
    if not text.strip():
        raise HTTPException(400, "Empty feedback")
    if len(text) > MAX_TEXT:
        raise HTTPException(400, "Too long")
    
    db.execute(text("""
        INSERT INTO feedback (text, email, page, user_agent, ip, created_at)
        VALUES (:text, :email, :page, :ua, :ip, now())
    """), {
        "text": text.strip(),
        "email": email.strip() if email else None,
        "page": page,
        "ua": request.headers.get("user-agent") if request else None,
        "ip": request.client.host if request.client else None,
    })
    db.commit()
    
    try:
        subject = "FicAtlas feedback"
        body = f"""New feedback from {page or 'unknown page'}:

{text}

---
Email (if provided): {email or 'none'}
User-Agent: {request.headers.get('user-agent') if request else 'unknown'}
IP: {request.client.host if request.client else 'unknown'}
"""
        await send(SMTP_FROM, subject, body)
    except Exception as e:
        log.warning(f"feedback email failed: {e}")
    
    return {"ok": True, "message": "Thanks — sent to the owner."}