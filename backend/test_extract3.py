import re, logging
logging.basicConfig(level=logging.DEBUG)
from db.session import get_db
import api.search as S

db = next(get_db())
S._CWC_CACHE, S._CWC_AT = set(), 0.0
S._ALIAS_CACHE, S._ALIAS_AT = set(), 0.0
S._PROSE_CACHE, S._PROSE_AT = set(), 0.0

# Monkey-patch to see what terms come into the probing loop
original_extract = S.extract
import functools

def debug_extract(text="", db=None):
    # We need to see the intermediate terms. Let's call the internal function
    # Actually, let's just trace it
    post = text
    words = S._tokenize_words(post)
    print(f"Words: {words}")
    print(f"Word count: {len(words)}")
    
    # Check what n-grams match
    from sqlalchemy import text as sql_text
    grams = S._grams_from_words(words)
    print(f"\nGrams with 'moody': {[(g, span) for g, span in grams.items() if 'moody' in g.lower()]}")
    print(f"Grams with 'marauder': {[(g, span) for g, span in grams.items() if 'marauder' in g.lower()]}")
    print(f"Grams with 'wizarding': {[(g, span) for g, span in grams.items() if 'wizarding' in g.lower()]}")
    
    # Check exact match for each
    for g in sorted(grams.keys(), key=lambda x: -len(x)):
        if 'moody' in g.lower() or 'marauder' in g.lower() or 'wizarding' in g.lower() or 'train' in g.lower():
            rows = db.execute(sql_text("""
                SELECT CASE WHEN kind = 'fandom_ao3' THEN 'fandom' ELSE kind END AS kind,
                       value, count
                  FROM facets
                 WHERE lower(value) = :g
                   AND count >= :min
            """), {"g": g.lower(), "min": 50}).fetchall()
            if rows:
                print(f"  Exact match '{g}': {rows}")
    
    resp = original_extract(text=text, db=db)
    print(f"\nResult Query: {resp.query!r}")
    print(f"Result Terms: {[(t.kind, t.value, t.count, t.matched) for t in resp.terms]}")
    print(f"Ignored: {resp.ignored_words}")

debug_extract(post="fic set during first wizarding war where mad eye moody trains marauders", db=db)
db.close()
