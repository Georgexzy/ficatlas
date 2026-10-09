from api.search import _probe_count, ExtractedTerm, _canonical_character
from db.session import get_db
import api.search as S
db = next(get_db())
S._CWC_CACHE, S._CWC_AT = set(), 0.0
S._ALIAS_CACHE, S._ALIAS_AT = set(), 0.0

moody_val = 'Alastor "Mad-Eye" Moody'
print("Testing character:", repr(moody_val))

t = ExtractedTerm(kind="character", value=moody_val, count=2800, matched="mad eye moody")
n0 = _probe_count(db, [t], None, None, None)
print(f"Alastor Moody alone: {n0} works")

t2 = ExtractedTerm(kind="character", value="Marauders (Harry Potter)", count=2109, matched="marauders")
n2 = _probe_count(db, [t2, t], None, None, None)
print(f"Marauders + Alastor Moody: {n2} works")

t3 = ExtractedTerm(kind="tag", value="Trains", count=2532, matched="trains")
n3 = _probe_count(db, [t2, t3], None, None, None)
print(f"Marauders + Trains: {n3} works")

t4 = ExtractedTerm(kind="tag", value="moody", count=75, matched="moody")
n4 = _probe_count(db, [t2, t4], None, None, None)
print(f"Marauders + moody tag: {n4} works")

# Also test the original query
out = S.extract(text="fic set during first wizarding war where mad eye moody trains marauders", db=db)
print()
print("Full extract output:")
for t in out.terms:
    print(f"  {t.kind}: {t.value} ({t.count})")
print("Query:", out.query)
print("Ignored:", out.ignored_words)

db.close()
