"""SQLAlchemy models for FicAtlas"""
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, DateTime,
    Text, ARRAY, ForeignKey, Enum as SAEnum, Index, create_engine
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
import enum
import uuid

Base = declarative_base()


class SiteEnum(str, enum.Enum):
    ao3                = "ao3"
    ffnet              = "ffnet"
    fictionalley       = "fictionalley"
    royalroad          = "royalroad"
    spacebattles       = "spacebattles"
    sufficientvelocity = "sufficientvelocity"


class RatingEnum(str, enum.Enum):
    general   = "G"
    teen      = "T"
    mature    = "M"
    explicit  = "E"
    not_rated = "NR"


class StatusEnum(str, enum.Enum):
    complete    = "complete"
    in_progress = "in_progress"
    abandoned   = "abandoned"
    unknown     = "unknown"


class Story(Base):
    __tablename__ = "stories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site = Column(SAEnum(SiteEnum), nullable=False, index=True)
    site_id = Column(String(64), nullable=False)
    url = Column(Text, nullable=False, unique=True)

    title = Column(Text, nullable=False)
    author = Column(String(255))
    author_url = Column(Text)
    summary = Column(Text)
    language = Column(String(32), default="English", index=True)

    rating = Column(SAEnum(RatingEnum), default=RatingEnum.not_rated, index=True)
    status = Column(SAEnum(StatusEnum), default=StatusEnum.unknown, index=True)
    is_crossover = Column(Boolean, default=False)

    word_count = Column(BigInteger, default=0, index=True)
    chapter_count = Column(Integer, default=1)
    chapter_count_total = Column(Integer)

    kudos = Column(Integer, default=0)
    bookmarks = Column(Integer, default=0)
    hits = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    favourites = Column(Integer, default=0)

    fandoms = Column(ARRAY(Text), default=list)
    characters = Column(ARRAY(Text), default=list)
    relationships = Column(ARRAY(Text), default=list)
    tags = Column(ARRAY(Text), default=list)
    warnings = Column(ARRAY(Text), default=list)
    categories = Column(ARRAY(Text), default=list)
    genres = Column(ARRAY(Text), default=list)

    ao3_archive_warnings = Column(ARRAY(Text), default=list)
    ffnet_category = Column(String(128))
    # Which section of a multi-part archive this came from — FictionAlley's
    # Schnoogle / Dark Arts / Astronomy Tower / Riddikulus, and equivalents.
    archive_section = Column(String(64))

    # Denormalised: true when this work appears in series_works. The in_series
    # search filter used to be an EXISTS subquery that forced a seq scan of
    # series_works and nested-loop lookup of every member — 15s for a Harry
    # Potter + AO3/FFN search. A plain boolean AND the other filters is ~1s.
    has_series = Column(Boolean, default=False, nullable=False)

    # New: hosted content
    is_hosted = Column(Boolean, default=False, index=True)   # we have the full text
    # Set when an author asks for their text not to be hosted here. The row
    # stays — only the full text stops being served. See api/takedown.py.
    text_withdrawn_at = Column(DateTime(timezone=True))
    # The listing itself withdrawn, not just the text. See init_db.py.
    delisted_at       = Column(DateTime(timezone=True))
    delisted_reason   = Column(Text)
    text_withdrawn_reason = Column(Text)
    wayback_url = Column(Text)                                # archive.org fallback
    # URLs where this same story is cross-posted on other sites (FFN/AO3/etc).
    # Populated when a curator (DLP, manual) tells us "these point to the same work".
    cross_post_urls = Column(ARRAY(Text), default=list)

    published_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), index=True)
    crawled_at = Column(DateTime(timezone=True), server_default=func.now())
    indexed_at = Column(DateTime(timezone=True), server_default=func.now())

    search_vector = Column(Text)

    chapters = relationship("Chapter", back_populates="story", cascade="all, delete-orphan",
                            order_by="Chapter.number")

    __table_args__ = (
        Index("ix_stories_site_site_id", "site", "site_id", unique=True),
        Index("ix_stories_word_count", "word_count"),
        Index("ix_stories_updated_at", "updated_at"),
        Index("ix_stories_fandoms", "fandoms", postgresql_using="gin"),
        Index("ix_stories_tags", "tags", postgresql_using="gin"),
        Index("ix_stories_relationships", "relationships", postgresql_using="gin"),
        Index("ix_stories_characters", "characters", postgresql_using="gin"),
    )


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_id = Column(UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    number = Column(Integer, nullable=False)
    title = Column(Text)
    summary = Column(Text)
    content = Column(Text, nullable=False)   # the body (HTML or plain text)
    word_count = Column(Integer, default=0)
    posted_at = Column(DateTime(timezone=True))
    start_note = Column(Text)
    end_note = Column(Text)

    story = relationship("Story", back_populates="chapters")

    __table_args__ = (
        Index("ix_chapters_story_number", "story_id", "number", unique=True),
    )


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site = Column(SAEnum(SiteEnum), nullable=False)
    job_type = Column(String(32))
    status = Column(String(32), default="pending")
    stories_found = Column(Integer, default=0)
    stories_new = Column(Integer, default=0)
    stories_updated = Column(Integer, default=0)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def get_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)


def create_tables(engine):
    Base.metadata.create_all(engine)
