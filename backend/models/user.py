"""User accounts, sessions, and per-account JSON storage."""
from datetime import datetime
import uuid
from sqlalchemy import Boolean, Column, String, DateTime, ForeignKey, Index
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
    # Optional. Existing accounts predate it, and a home-hosted site cannot
    # promise mail delivery, so nothing may depend on it being present.
    email         = Column(String(200))

    def effective_role(self) -> str:
        """The role this REQUEST runs as, which is not always the stored one.

        A session can ask to be seen as a lesser role (see auth.get_current_user).
        That choice is carried on the instance as `_view_as`, deliberately not as
        a column: `role` is mapped, so writing the preview there would let any
        commit in the same request flush the demotion to the database and make it
        permanent. Everything that asks what you may do goes through here, so the
        preview applies without anything being written down.
        """
        return getattr(self, "_view_as", None) or self.role or ROLE_READER

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.effective_role(), 0)

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
    # See init_db.py: the cookie cannot be asked whether it was persistent.
    remember    = Column(Boolean, nullable=False, server_default="true")
    # Temporary self-downgrade for previewing the site as a lesser role.
    # Never an upgrade — see auth.get_current_user.
    view_as     = Column(String(16))


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
