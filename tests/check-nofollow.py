#!/usr/bin/env python3
"""Every link into the robots-blocked query space must be rel="nofollow".

robots.txt says `Disallow: /*?` — the search UI is an infinite space of filter
combinations backed by a query over 20M rows, and letting a crawler walk it is
the single most likely way this site falls over. But blocking a URL does not
stop Google DISCOVERING it: a followed link is queued, fetched-and-refused, and
then reported. Measured 2026-09-06, before this check existed:

    /ship/castiel-dean-winchester   105 links into /?…   0 nofollowed
    /fandom/harry-potter            157 links into /?…   0 nofollowed
    /story/<id>                      40 links into /?…   0 nofollowed

Across 11,190 hubs that is on the order of a million discovered-and-blocked
URLs, which is what Search Console reports as "Blocked by robots.txt", and it
is spent out of a Googlebot budget measured at 119 requests a day.

nofollow costs nothing here because these URLs are unreachable to a crawler
either way. The links a crawler SHOULD follow — /fandom/…, /ship/…, /story/… —
are untouched and are checked for separately by their own pages.

Run: python3 tests/check-nofollow.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "frontend" / "app"

# A <Link …> or <a …> element whose href goes to the query space. Non-greedy to
# the first ">", which is what bounds the opening tag.
_LINK = re.compile(r"<(?:Link|a)\s[^>]*?>", re.S)
_TO_QUERY = re.compile(r'href=(?:\{[`"]/\?|"/\?|\{searchHref\()')


def main() -> int:
    bad = []
    for path in sorted(APP.rglob("*.tsx")):
        src = path.read_text()
        for m in _LINK.finditer(src):
            tag = m.group(0)
            if not _TO_QUERY.search(tag):
                continue
            if 'rel="nofollow"' in tag or "rel={" in tag:
                continue
            line = src[: m.start()].count("\n") + 1
            bad.append(f"{path.relative_to(ROOT)}:{line}  {' '.join(tag.split())[:100]}")

    if bad:
        print("Links into the robots-blocked /?… space without rel=\"nofollow\":\n")
        for b in bad:
            print("  " + b)
        print(f"\n{len(bad)} link(s). Add rel=\"nofollow\" — see this file's docstring.")
        return 1
    print("All links into the /?… query space are nofollowed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
