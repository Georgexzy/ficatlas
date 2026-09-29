"""Does an archive LINK catch what a byline would? Across several real threads."""
import html, json, os, re, sys
sys.path.insert(0, "/app")
from db.dsn import default_database_url
os.environ.setdefault("DATABASE_URL", default_database_url())
import logging; logging.basicConfig(level=logging.INFO, format="%(message)s")
from sqlalchemy import text
from db.session import db_session
import reddit_fetch as rf

SUB = "HPFanfiction"
SEARCH = ("https://www.reddit.com/r/{sub}/search.rss?q=%22what+are+you+reading%22"
          "&restrict_sr=1&sort=new&limit=25")

LINK = re.compile(r"(?:fanfiction\.net/s/(\d+)|archiveofourown\.org/works/(\d+))", re.I)
BOUNDARY = re.compile(r"[\n\r]|[.;:!?]\s|\(\s*\d+(?:\.\d+)?\s*/\s*\d+\s*\)|\s[-–—]\s|^\s*\d+[.)]\s", re.X | re.M)

def plain(h): return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))

def bylines(t):
    for m in re.finditer(r"\s+b[ye]\s+", t):
        left = t[:m.start()]
        cuts = [b.end() for b in BOUNDARY.finditer(left)]
        title = left[max(cuts) if cuts else 0:].strip(" *_\"'“”‘’[]()")
        am = re.match(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{1,30}", t[m.end():])
        if am and 3 <= len(title) and len(title.split()) <= 12:
            yield title, am.group(0)

def entries(xml):
    return re.findall(r"<entry>(.*?)</entry>", xml, re.S)

try:
    feed = rf.get(SEARCH.format(sub=SUB))
except rf.Refused as e:
    print("search refused:", e); raise SystemExit(1)

threads = []
for e in entries(feed):
    u = re.search(r'<link href="(.*?)"', e); d = re.search(r"<published>(.*?)</published>", e)
    t = re.search(r"<title>(.*?)</title>", e, re.S)
    if not (u and d): continue
    m = re.search(r"/comments/([a-z0-9]+)/", u.group(1))
    if m and "what are you reading" in (t.group(1) if t else "").lower():
        threads.append((d.group(1)[:10], m.group(1)))
print(f"found {len(threads)} 'what are you reading' threads")

WANT = int(os.getenv("WANT", "5"))
tot_link_works = tot_byline_new = tot_byline_dup = tot_byline_unres = 0
per_thread = []
with db_session() as db:
    for when, pid in threads[:WANT]:
        try:
            xml = rf.get(f"https://www.reddit.com/r/{SUB}/comments/{pid}.rss?limit=500")
        except rf.Refused as e:
            print(f"  {when} {pid}: refused ({e}) — stopping"); break
        ents = entries(xml)
        body = ""
        for e in ents:
            c = re.search(r'<content type="html">(.*?)</content>', e, re.S)
            a = re.search(r"<name>(.*?)</name>", e, re.S)
            if c and (not a or a.group(1) != "/u/AutoModerator"):
                body += html.unescape(html.unescape(c.group(1))) + "\n"
        ids = set()
        for m in LINK.finditer(body):
            site, sid = ("ffnet", m.group(1)) if m.group(1) else ("ao3", m.group(2))
            r = db.execute(text("SELECT id FROM stories WHERE site=:s AND site_id=:i"),
                           {"s": site, "i": sid}).first()
            if r: ids.add(str(r[0]))
        new = dup = unres = 0
        for title, author in bylines(plain(body)):
            rows = db.execute(text("""SELECT id, author FROM stories WHERE lower(title)=lower(:t)
                                      ORDER BY COALESCE(kudos,0) DESC LIMIT 20"""), {"t": title}).fetchall()
            exact = [r for r in rows if (r[1] or "").lower() == author.lower()]
            if len(exact) == 1:
                if str(exact[0][0]) in ids: dup += 1
                else: new += 1; print(f"      BYLINE-ONLY: {title!r} by {author}")
            else: unres += 1
        print(f"  {when}  {len(ents):3d} comments  {len(ids):3d} works via links"
              f"   bylines: {dup} already-linked, {new} NEW, {unres} unresolved")
        per_thread.append((when, len(ents), len(ids), new))
        tot_link_works += len(ids); tot_byline_new += new
        tot_byline_dup += dup; tot_byline_unres += unres

print(f"\nTOTAL over {len(per_thread)} threads:")
print(f"  works found by archive link : {tot_link_works}")
print(f"  works bylines ADD over links: {tot_byline_new}")
print(f"  bylines that were already linked: {tot_byline_dup}")
print(f"  bylines that resolved to nothing: {tot_byline_unres}")
