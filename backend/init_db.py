"""Run this once to create tables and set up full-text search triggers.
Usage: DATABASE_URL=... python init_db.py
"""
import os
from sqlalchemy import text
from models.story import Base, get_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ficatlas:ficatlas@localhost:5432/ficatlas")
engine = get_engine(DATABASE_URL)

def init():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        # Full-text search vector trigger
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_stories_search_vector
            ON stories USING gin(to_tsvector('english',
                coalesce(title,'') || ' ' ||
                coalesce(summary,'') || ' ' ||
                coalesce(author,'')
            ));
        """))

        # Partial index for explicit content filtering
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_stories_non_explicit
            ON stories (updated_at DESC)
            WHERE rating != 'E';
        """))
        conn.commit()
    print("Database initialised.")

if __name__ == "__main__":
    init()
