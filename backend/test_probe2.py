from api.search import _probe_count, ExtractedTerm
from db.session import get_db
import api.search as S
db = next(get_db())
S._CWC_CACHE, S._CWC_AT = set(), 0.0
S._ALIAS_CACHE, S._ALIAS_AT = set(), 0.0

marauders = ExtractedTerm(kind="character", value="Marauders (Harry Potter)", count=2109, matched="marauders")
fww = ExtractedTerm(kind="tag", value="first wizarding war", count=293, matched="first wizarding war")
trains = ExtractedTerm(kind="tag", value="Trains", count=2532, matched="trains")
moody_char = ExtractedTerm(kind="character", value='Alastor "Mad-Eye" Moody', count=2800, matched="mad eye moody")
moody_tag = ExtractedTerm(kind="tag", value="moody", count=75, matched="moody")

combos = [
    ("Marauders alone", [marauders]),
    ("Marauders + FWW", [marauders, fww]),
    ("Marauders + Alastor", [marauders, moody_char]),
    ("Marauders + FWW + Alastor", [marauders, fww, moody_char]),
    ("Marauders + Trains", [marauders, trains]),
    ("Marauders + FWW + Trains", [marauders, fww, trains]),
    ("Alastor alone", [moody_char]),
    ("Alastor + FWW", [moody_char, fww]),
    ("Marauders + FWW + moody tag", [marauders, fww, moody_tag]),
]

for label, terms_list in combos:
    n = _probe_count(db, terms_list, None, None, None)
    print(f"{label}: {n} works")

db.close()
