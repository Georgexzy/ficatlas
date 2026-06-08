"""SQLAlchemy models for FicAtlas"""
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, DateTime,
    Text, ARRAY, Float, Enum as SAEnum, Index, create_engine
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
import enum
import uuid

Base = declarative_base()


class SiteEnum(str, enum.Enum):
    ao3 = "ao3"
    ffnet = "ffnet"
    wattpad = "wattpad"
    royalroad = "royalroad"
    spacebattles = "spacebattles"
    sufficientvelocity = "sufficientvelocity"


class RatingEnum(str, enum.Enum):
    general = "G"
    teen = "T"
    mature = "M"
    explicit = "E"
    not_rated = "NR"


class StatusEnum(str, enum.Enum):
    complete = "complete"
    in_progress = "in_progress"
    abandoned = "abandoned"
    unknown = "unknown"


class Story(Base):
    __tablename__ = "stories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site = Column(SAEnum(SiteEnum), nullable=False, index=True)
    site_id = Column(String(64), nullable=False)          # native ID on the source site
    url = Column(Text, nullable=False, unique=True)

    # Core metadata
    title = Column(Text, nullable=False)
    author = Column(String(255))
    author_url = Column(Text)
    summary = Column(Text)
    language = Column(String(32), default="English", index=True)

    # Classification
    rating = Column(SAEnum(RatingEnum), default=RatingEnum.not_rated, index=True)
    status = Column(SAEnum(StatusEnum), default=StatusEnum.unknown, index=True)
    is_crossover = Column(Boolean, default=False)

    # Counts
    word_count = Column(BigInteger, default=0, index=True)
    chapter_count = Column(Integer, default=1)
    chapter_count_total = Column(Integer)  # None = unknown / ?

    # Popularity
    kudos = Column(Integer, default=0)
    bookmarks = Column(Integer, default=0)
    hits = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    favourites = Column(Integer, default=0)  # FF.net style

    # Tags (PostgreSQL arrays for fast overlap queries)
    fandoms = Column(ARRAY(Text), default=list)
    characters = Column(ARRAY(Text), default=list)
    relationships = Column(ARRAY(Text), default=list)
    tags = Column(ARRAY(Text), default=list)
    warnings = Column(ARRAY(Text), default=list)
    categories = Column(ARRAY(Text), default=list)   # F/F, F/M, M/M, Gen, etc.
    genres = Column(ARRAY(Text), default=list)       # FF.net genres

    # Site-specific extras (stored as JSON-safe text)
    ao3_archive_warnings = Column(ARRAY(Text), default=list)
    ffnet_category = Column(String(128))

    # Timestamps
    published_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), index=True)
    crawled_at = Column(DateTime(timezone=True), server_default=func.now())
    indexed_at = Column(DateTime(timezone=True), server_default=func.now())

    # Full-text search vector (populated by trigger or background job)
    search_vector = Column(Text)  # tsvector stored as text; use raw SQL for GIN index

    __table_args__ = (
        Index("ix_stories_site_site_id", "site", "site_id", unique=True),
        Index("ix_stories_word_count", "word_count"),
        Index("ix_stories_updated_at", "updated_at"),
        Index("ix_stories_fandoms", "fandoms", postgresql_using="gin"),
        Index("ix_stories_tags", "tags", postgresql_using="gin"),
        Index("ix_stories_relationships", "relationships", postgresql_using="gin"),
        Index("ix_stories_characters", "characters", postgresql_using="gin"),
    )


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site = Column(SAEnum(SiteEnum), nullable=False)
    job_type = Column(String(32))  # "full", "incremental", "single"
    status = Column(String(32), default="pending")  # pending, running, done, failed
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
