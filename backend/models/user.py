"""User accounts, sessions, and per-account JSON storage."""
from datetime import datetime
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from models.story import Base


# Roles, least to most privileged.
#
# Before this there was one tier: "logged in". require_admin only checked that
# a session existed, so anyone who signed up could trigger archive scrapes, run
# bulk imports and DELETE hosted full text. That is fine for a single-user box
# on a tailnet and unusable the moment a second person has an account.
#
#   reader  search, read, bookmark, sync own data. Cannot change the library.
#   admin   the above, plus imports and scrapes.
#   owner   the above, plus destructive cleanup and managing other accounts.
#
# Scraping sits at admin rather than reader on purpose: those requests leave
# from the host's IP, so a reader triggering them spends the operator's
# reputation with AO3 and FF.net, not their own.
ROLE_READER = "reader"
ROLE_ADMIN  = "admin"
ROLE_OWNER  = "owner"
ROLE_RANK = {ROLE_READER: 0, ROLE_ADMIN: 1, ROLE_OWNER: 2}


class User(Base):
    __tablename__ = "users"
    id            = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login    = Column(DateTime)
    # New accounts are readers. The first account ever created becomes owner —
    # see init_db, which also promotes the oldest existing account when this
    # column is added to an instance that predates it.
    role          = Column(String(16), nullable=False, server_default=ROLE_READER)

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.role or ROLE_READER, 0)

    def at_least(self, role: str) -> bool:
        return self.rank >= ROLE_RANK.get(role, 99)


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
