"""Story detail + chapter reading endpoints"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from db.session import get_db
from models.story import Story, Chapter

router = APIRouter()


class ChapterMeta(BaseModel):
    id: str
    number: int
    title: Optional[str]
    word_count: int
    posted_at: Optional[str]


class ChapterFull(BaseModel):
    id: str
    number: int
    title: Optional[str]
    summary: Optional[str]
    content: str
    word_count: int
    posted_at: Optional[str]
    start_note: Optional[str]
    end_note: Optional[str]


class StoryDetail(BaseModel):
    id: str
    site: str
    url: str
    title: str
    author: str
    author_url: Optional[str]
    summary: Optional[str]
    language: str
    rating: Optional[str]
    status: str
    word_count: int
    chapter_count: int
    chapter_count_total: Optional[int]
    kudos: int
    hits: int
    bookmarks: int
    comments: int
    fandoms: List[str]
    relationships: List[str]
    characters: List[str]
    tags: List[str]
    warnings: List[str]
    categories: List[str]
    genres: List[str]
    published_at: Optional[str]
    updated_at: Optional[str]
    is_hosted: bool
    wayback_url: Optional[str]
    cross_post_urls: List[str] = []
    chapters: List[ChapterMeta]


@router.get("/{story_id}", response_model=StoryDetail)
async def get_story(story_id: str, db: Session = Depends(get_db)):
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")

    chapter_meta = [
        ChapterMeta(
            id=str(c.id),
            number=c.number,
            title=c.title,
            word_count=c.word_count or 0,
            posted_at=c.posted_at.isoformat() if c.posted_at else None,
        )
        for c in (story.chapters or [])
    ]

    return StoryDetail(
        id=str(story.id),
        site=story.site.value,
        url=story.url,
        title=story.title,
        author=story.author or "Anonymous",
        author_url=story.author_url,
        summary=story.summary,
        language=story.language or "English",
        rating=story.rating.value if story.rating else None,
        status=story.status.value if story.status else "unknown",
        word_count=story.word_count or 0,
        chapter_count=story.chapter_count or 1,
        chapter_count_total=story.chapter_count_total,
        kudos=story.kudos or 0,
        hits=story.hits or 0,
        bookmarks=story.bookmarks or 0,
        comments=story.comments or 0,
        fandoms=story.fandoms or [],
        relationships=story.relationships or [],
        characters=story.characters or [],
        tags=story.tags or [],
        warnings=story.warnings or [],
        categories=story.categories or [],
        genres=story.genres or [],
        published_at=story.published_at.isoformat() if story.published_at else None,
        updated_at=story.updated_at.isoformat() if story.updated_at else None,
        is_hosted=story.is_hosted or False,
        wayback_url=story.wayback_url,
        cross_post_urls=story.cross_post_urls or [],
        chapters=chapter_meta,
    )


@router.get("/{story_id}/chapters/{number}", response_model=ChapterFull)
async def get_chapter(story_id: str, number: int, db: Session = Depends(get_db)):
    chapter = (db.query(Chapter)
               .filter(Chapter.story_id == story_id, Chapter.number == number)
               .first())
    if not chapter:
        raise HTTPException(404, "Chapter not found")

    return ChapterFull(
        id=str(chapter.id),
        number=chapter.number,
        title=chapter.title,
        summary=chapter.summary,
        content=chapter.content,
        word_count=chapter.word_count or 0,
        posted_at=chapter.posted_at.isoformat() if chapter.posted_at else None,
        start_note=chapter.start_note,
        end_note=chapter.end_note,
    )
