#!/usr/bin/env bash
# FicAtlas smoke tests — every route, plus the flows that span several systems.
#
# Runs against a running stack (docker compose up -d) and needs nothing but curl
# and python3. It is deliberately black-box: it exercises the app the way a
# browser does, through the frontend's /api proxy on port 3000.
#
# This exists because a session of manual auditing turned up problems that only
# an end-to-end sweep would find:
#   - a TypeError in the AO3 URL builder meant live fetch had NEVER worked, and
#     the exception was swallowed, so search looked fine
#   - tags=Fluff, page=3 and sort=word_count_desc returned 500 (exhausted the
#     container's 64MB /dev/shm) while every other query was fine
#   - /similar and /export.epub were documented and wired into the UI but had
#     never been implemented, and the frontend swallowed the 404
#
# Usage:  ./tests/smoke.sh [base-url]
# Exit:   0 all passed, 1 otherwise.

set -uo pipefail
B="${1:-http://localhost:3000}"
pass=0; fail=0; failed_names=()

ok()   { pass=$((pass+1)); printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { fail=$((fail+1)); failed_names+=("$1"); printf "  \033[31m✗\033[0m %s — got %s, want %s\n" "$1" "$2" "$3"; }
chk()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }
code() { curl -s -o /dev/null -w "%{http_code}" --max-time 60 "$1"; }
head_() { printf "\n\033[1m%s\033[0m\n" "$1"; }

command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
if [ "$(code "$B/api/stats/totals")" != "200" ]; then
  echo "Stack not reachable at $B — start it with: docker compose up -d"; exit 1
fi

head_ "Pages"
for p in / /library /settings /account /login; do chk "page $p" "$(code "$B$p")" 200; done
chk "dynamic story shell (any id)" "$(code "$B/story/offline-shell/chapter/1")" 200
chk "service worker" "$(code "$B/sw.js")" 200
chk "web manifest"   "$(code "$B/manifest.json")" 200

head_ "Search — filters and sorts"
# Each of these has broken at some point; they are not arbitrary.
for q in "q=dramione" "q=%22time+travel%22" "q=harry+-potter" \
         "fandoms=Harry+Potter" "relationships=Draco/Hermione" "characters=Hermione" \
         "tags=Fluff" "author=SilentAuror" "status=complete" "ratings=M" \
         "word_count_min=50000" "exclude_tags=Fluff" "crossovers=only" \
         "include_unknown=true&tags=Fluff" "sites=fictionalley" \
         "page=3&q=harry" "sort=word_count_desc&q=harry" "sort=updated_desc" ; do
  chk "search $q" "$(code "$B/api/search?$q&live=false")" 200
done
chk "random discovery" "$(code "$B/api/search/random?count=5")" 200

head_ "Search — correctness"
# Strict filtering: every result must genuinely carry the ship. This regressed
# from "5001 results, none of which had it" to 97 real matches.
res=$(curl -s --max-time 60 "$B/api/search?relationships=Draco/Hermione&live=false&per_page=50")
chk "ship filter returns only real matches" "$(printf '%s' "$res" | python3 -c '
import sys,json,re
d=json.load(sys.stdin)
bad=0
for r in d["results"]:
    hit=False
    for rel in (r["relationships"] or []):
        if "/" not in rel and " x " not in rel: continue
        n=" ".join(re.split(r"/| x ",rel.lower()))
        if ("draco" in n or re.search(r"\bd\b",n)) and ("hermione" in n or re.search(r"\bhr\b",n)): hit=True
    if not hit: bad+=1
print("clean" if bad==0 else f"{bad} bogus")')" "clean"

# Provenance tags must never be suggested as content tags.
chk "autocomplete excludes provenance" "$(curl -s --max-time 30 "$B/api/stats/suggest?kind=tag&q=dump&limit=10" | python3 -c '
import sys,json
prov={"ffnet_dump","hf_meta_2024","ao3_meta_dump","janelleshane_seed"}
v={r["value"] for r in json.load(sys.stdin)}
print("clean" if not (v & prov) else "leaked")')" "clean"

head_ "Search syntax — operators must actually parse"
# `fandom: Harry Potter` is the README's headline example and did not parse at
# all: the value pattern required no space after the colon and stopped at the
# first whitespace, so the filter was silently dropped into free text.
syntax_check() { # label  raw-query  expected-key  expected-value
  got=$(curl -s --max-time 60 "$B/api/search?q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$2")&live=false" \
        | python3 -c "
import sys,json
toks=json.load(sys.stdin)['parsed_tokens']
print(next((t['value'] for t in toks if t['key']=='$3'), 'MISSING'))")
  chk "$1" "$got" "$4"
}
syntax_check "fandom: spaced multi-word"  "fandom: Harry Potter"          fandoms       "Harry Potter"
syntax_check "fandom: unspaced multi-word" "fandom:Harry Potter"          fandoms       "Harry Potter"
syntax_check "char: spaced multi-word"    "char: Hermione Granger"        characters    "Hermione Granger"
syntax_check "tag: spaced multi-word"     "tag: slow burn"                tags          "slow burn"
syntax_check "quoted value"               'fandom:"Harry Potter"'         fandoms       "Harry Potter"
syntax_check "ship operator"              "ship:Draco/Hermione"           relationships "Draco/Hermione"
# Trailing shorthand must stay a shorthand, not get eaten by the fandom value.
syntax_check "shorthand after value"      "fandom: Harry Potter complete" fandoms       "Harry Potter"
syntax_check "status from shorthand"      "fandom: Harry Potter complete" status        "complete"
# A value that IS a shorthand word must survive.
syntax_check "status:complete"            "status:complete"               status        "complete"

head_ "Multi-value filter modes"
# Values inside one filter were always ANDed with nothing saying so — right for
# crossovers, wrong when one fandom is split across several spellings.
chk "match_mode=all narrows (crossovers)" "$(curl -s --max-time 60 \
  "$B/api/search?fandoms=Discworld,Good+Omens&match_mode=all&live=false" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("narrow" if not d["count_is_capped"] and d["total"]>0 else "wrong")')" "narrow"
chk "match_mode=any widens (union)" "$(curl -s --max-time 60 \
  "$B/api/search?fandoms=Discworld,Good+Omens&match_mode=any&live=false" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("wide" if d["count_is_capped"] or d["total"]>1000 else "wrong")')" "wide"
# AO3 writes "Work - Author" while FF.net writes the bare name, so the same
# fandom is split; picking the canonical form must not exclude the short one.
chk "fandom variants resolve alike" "$(python3 -c '
import json,urllib.request
def n(f):
    u="'"$B"'/api/search?live=false&sites=ffnet&fandoms="+urllib.request.quote(f)
    return json.load(urllib.request.urlopen(u))["total"]
a=n("Harry Potter"); b=n("Harry Potter - J. K. Rowling")
print("same" if a==b else f"{a} vs {b}")')" "same"

head_ "Stats"
for e in "stats/totals" "stats/sites" "stats/suggest?kind=fandom&q=harry" \
         "stats/suggest?kind=relationship&q=draco" "stats/suggest?kind=character&q=her"; do
  chk "$e" "$(code "$B/api/$e")" 200
done

head_ "Story, reader, offline dependencies"
SID=$(curl -s --max-time 60 "$B/api/search?sites=fictionalley&live=false" \
      | python3 -c 'import sys,json;r=json.load(sys.stdin)["results"];print(r[0]["id"] if r else "")')
if [ -z "$SID" ]; then
  bad "hosted story available" "none" "one"
else
  for e in "" "/chapters/1" "/similar" "/export.epub"; do
    chk "story$e" "$(code "$B/api/stories/$SID$e")" 200
  done
  chk "story page"  "$(code "$B/story/$SID")" 200
  chk "reader page" "$(code "$B/story/$SID/chapter/1")" 200

  # The EPUB must be a valid archive, not merely a 200. Chapter HTML from the
  # scrapers is not well-formed XHTML on its own and silently produced books
  # whose chapters would not parse.
  chk "EPUB is a valid, parseable book" "$(curl -s --max-time 60 "$B/api/stories/$SID/export.epub" | python3 -c '
import sys,zipfile,io,xml.dom.minidom as md
try:
    z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
    assert z.namelist()[0]=="mimetype"
    assert z.getinfo("mimetype").compress_type==zipfile.ZIP_STORED
    for n in z.namelist():
        if n.endswith((".xhtml",".opf",".ncx")): md.parseString(z.read(n))
    print("valid")
except Exception as e: print(f"invalid: {e}")')" "valid"

  # Offline save walks the chapters a story ACTUALLY has. Declared chapter_count
  # disagrees on real rows, and looping over it aborted the whole save.
  chk "every listed chapter is fetchable" "$(curl -s --max-time 60 "$B/api/stories/$SID" | python3 -c "
import sys,json,urllib.request
d=json.load(sys.stdin)
for c in d['chapters']:
    if urllib.request.urlopen('$B/api/stories/$SID/chapters/%d'%c['number']).status!=200:
        print('missing'); break
else: print('all')")" "all"
fi

head_ "Auth, sync and account lifecycle"
U="smoke_$RANDOM$$"; J=$(mktemp); K=$(mktemp)
curl -s -c "$J" -X POST -d "username=$U&password=smokepw123" "$B/api/auth/signup" >/dev/null
chk "signup + session cookie" "$(curl -s -b "$J" "$B/api/auth/me" | python3 -c 'import sys,json;u=json.load(sys.stdin)["user"];print(u["username"] if u else "none")')" "$U"

curl -s -b "$J" -X POST -H 'Content-Type: application/json' \
  -d '{"bookmarks":["s1","s2"],"progress":{"s1":{"chapter":3,"at":"2026-01-01T00:00:00"}}}' \
  "$B/api/userdata/merge" >/dev/null
# A second device with NO local state must pull the account down, and a stale
# device must not clobber newer progress from another one.
curl -s -c "$K" -X POST -d "username=$U&password=smokepw123" "$B/api/auth/login" >/dev/null
curl -s -b "$K" -X POST -H 'Content-Type: application/json' \
  -d '{"progress":{"s1":{"chapter":9,"at":"2026-06-01T00:00:00"}}}' "$B/api/userdata/merge" >/dev/null
chk "stale device cannot clobber newer progress" "$(curl -s -b "$J" -X POST -H 'Content-Type: application/json' \
  -d '{"progress":{"s1":{"chapter":3,"at":"2026-01-01T00:00:00"}}}' "$B/api/userdata/merge" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["progress"]["s1"]["chapter"])')" "9"
chk "bookmarks survive the merge" "$(curl -s -b "$K" -X POST -H 'Content-Type: application/json' -d '{}' \
  "$B/api/userdata/merge" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("bookmarks",[])))')" "2"

head_ "Destructive admin paths"
# cleanup-seeds deletes rows. Its matcher has been wrong in both directions: it
# once missed the fixtures entirely (wrong site_id pattern), and a later fix
# widened the pattern enough to match site_id 1234000 — a genuine AO3 work,
# "Fortune Teller" by Margo_Kim — which it would have deleted. A dry run must
# never propose removing anything that is real.
chk "cleanup-seeds proposes nothing real" "$(curl -s -b "$J" -X DELETE \
  "$B/api/library/admin/cleanup-seeds?dry_run=true" | python3 -c '
import sys,json
d=json.load(sys.stdin)
bad=[r for r in d.get("removed",[]) if r.get("site_id") not in
     {f"123400{n}" for n in range(1,9)}]
print("clean" if not bad else "would delete real rows")')" "clean"

head_ "Roles"
# There used to be one tier — "logged in" — so any account could scrape, import
# and delete hosted text. These check the three tiers actually hold. The signed
# -in account here is a fresh signup, which is a READER.
chk "import-url rejects anonymous"       "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d 'url=x' "$B/api/library/import-url")" 401
chk "cleanup-seeds rejects anonymous"    "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$B/api/library/admin/cleanup-seeds")" 401
chk "new signup is a reader"             "$(curl -s -b "$J" "$B/api/auth/me" | python3 -c 'import sys,json;print(json.load(sys.stdin)["user"]["role"])')" "reader"
chk "reader cannot import"               "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' -X POST -d 'url=x' "$B/api/library/import-url")" 403
chk "reader cannot scrape"               "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' -X POST "$B/api/library/discover-dlp")" 403
chk "reader cannot run cleanup"          "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' -X DELETE "$B/api/library/admin/cleanup-seeds?dry_run=true")" 403
chk "reader cannot list accounts"        "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "$B/api/auth/users")" 403
chk "reader keeps search"                "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "$B/api/search?q=harry&per_page=1")" 200
chk "reader keeps their own data"        "$(curl -s -b "$J" -o /dev/null -w '%{http_code}' "$B/api/userdata")" 200
chk "read-only stays public"            "$(code "$B/api/library/hosted?limit=2")" 200

curl -s -b "$J" -X POST -d "password=smokepw123" "$B/api/auth/delete-account" >/dev/null
chk "account deleted, session dead" "$(curl -s -b "$J" "$B/api/auth/me" | python3 -c 'import sys,json;print(json.load(sys.stdin)["user"])')" "None"
rm -f "$J" "$K"

printf "\n\033[1mRESULT\033[0m  passed=%d failed=%d\n" "$pass" "$fail"
if [ "$fail" -gt 0 ]; then
  printf "Failed:\n"; printf "  - %s\n" "${failed_names[@]}"; exit 1
fi
exit 0
