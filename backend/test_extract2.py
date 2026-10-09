import re
from db.session import get_db
from sqlalchemy import text as sql_text
import api.search as S

db = next(get_db())
S._CWC_CACHE, S._CWC_AT = set(), 0.0
S._ALIAS_CACHE, S._ALIAS_AT = set(), 0.0
S._PROSE_CACHE, S._PROSE_AT = set(), 0.0

_sq = lambda v: re.sub(r"[^a-z0-9]+", "", v.lower())
sq = _sq("mad eye moody")
print(f"Squashed 'mad eye moody': {sq!r}")

rows = db.execute(sql_text("""
    SELECT kind, value, count FROM facets
    WHERE kind = 'character'
    AND regexp_replace(
        lower(split_part(split_part(value, ' - ', 1),
                          ' (', 1)),
        '[^a-z0-9]+', '', 'g')
    LIKE :pattern
    AND count >= :min
"""), {"pattern": f"%{sq}", "min": 50}).fetchall()
print(f"Suffix squash results: {rows}")

# Check the exact match query too
rows2 = db.execute(sql_text("""
    SELECT kind, value, count FROM facets
    WHERE kind = 'character'
    AND lower(value) = 'alastor "mad-eye" moody'
    AND count >= 50
"""), {}).fetchall()
print(f"Exact match 'alastor \"mad-eye\" moody': {rows2}")

# Check what extract returns
from api.search import extract, ExtractResponse
post = "fic set during first wizarding war where mad eye moody trains marauders"
resp = extract(text=post, db=db)
print(f"\nQuery: {resp.query!r}")
print(f"Terms: {[(t.kind, t.value, t.count, t.matched) for t in resp.terms]}")
print(f"Ignored: {resp.ignored_words}")
db.close()
