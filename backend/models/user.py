"""User accounts, sessions, and per-account JSON storage."""
from datetime import datetime
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from models.story import Base


class User(Base):
    __tablename__ = "users"
    id            = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login    = Column(DateTime)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token       = Column(String(80), primary_key=True)
    user_id     = Column(PG_UUID(as_uuid=True),
                         ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at  = Column(DateTime, nullable=False)
    last_used   = Column(DateTime, default=datetime.utcnow)
    user_agent  = Column(String(255))


class UserData(Base):
    __tablename__ = "user_data"
    user_id    = Column(PG_UUID(as_uuid=True),
                        ForeignKey("users.id", ondelete="CASCADE"),
                        primary_key=True)
    key        = Column(String(50), primary_key=True)   # bookmarks | progress | recents | settings
    value      = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_user_data_user", "user_id"),
    )
