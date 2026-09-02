#!/usr/bin/env python3
"""
Fail on a robots.txt that a 1994-era parser reads differently from a modern one.

robots.txt has two specifications and they disagree about blank lines.

  RFC 9309 (2022)     a group runs from a User-agent line to the next User-agent
                      line. Empty lines carry no meaning.
  the 1994 draft      records are separated by blank lines.

Google, Bing and Amazon implement the first. Plenty of smaller crawlers — and
Python's own urllib.robotparser, which is a fair proxy for "somebody wrote this
in an afternoon" — implement the second. On this file both readings have to
produce the same rules, because the readings that matter are the naive ones:

    User-agent: *
                        <- a blank line here ENDS THE GROUP for a 1994 parser
    Disallow: /admin    <- attributed to no agent, and dropped
    ...
    Crawl-delay: 10     <- never reached

That was the shipped state. A legacy parser saw zero rules for `*`: no
`Disallow: /*?`, which is the only thing standing between a crawler and an
infinite space of filter URLs over a 19.7M-row table, and no Crawl-delay. The
crawlers most likely to parse this way are the small ones, which are exactly the
ones the crawler-trap defence is written for and the ones nobody would notice
arriving.

So the invariant is narrow and mechanical: no blank line may appear INSIDE a
user-agent group. Comments are fine — every parser strips them before testing
for an empty line — and do the spacing work. Blank lines BETWEEN groups are
correct under both readings and are left alone.

The second check is the one that would survive a rewrite of the first: parse the
file with the strict legacy parser and assert the rules that actually matter
still come out. A file can satisfy the blank-line rule and still be wrong.

WHAT THIS DELIBERATELY DOES NOT ASSERT
--------------------------------------
`*` and `$` in a path are RFC 9309 extensions and the 1994 grammar has no
equivalent, so a legacy parser reads `Disallow: /*?` and `Disallow:
/story/*/chapter/` as literal prefixes and matches nothing. That cannot be fixed
by writing the file differently — the older grammar simply cannot express "a URL
with a query string" — so those two rules are asserted to be PRESENT in the text
rather than asserted to take effect under the strict parser.

The residual exposure is a crawler that both ignores wildcards and follows
links: hub pages carry ~157 links into `/?…`, none of them nofollowed. It is
worth knowing about and it is currently theoretical — over a full day of logs,
all 157 requests to `/?…` came from browsers and from this project's own
scanner, and not one from Applebot, Amzn-SearchBot, SemrushBot, Googlebot or
YandexBot. Every crawler actually on this site honours the wildcard. If that
ever stops being true the fix is `rel="nofollow"` on those links, which no
grammar gets a vote on.

    python3 tests/check-robots.py
"""
import pathlib
import re
import sys
from urllib.robotparser import RobotFileParser

ROBOTS = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "public" / "robots.txt"

# (agent, url, expected allowed). Asserted against urllib.robotparser — the
# deliberately naive reader — so these prove the file survives the strict
# reading, not merely that Google would get it right.
EXPECTED = [
    # The crawler trap, and the private routes. These are the rules whose loss
    # is expensive and silent.
    ("Googlebot", "/admin", False),
    ("Googlebot", "/library", False),
    ("Googlebot", "/account", False),
    ("Googlebot", "/settings", False),
    ("Googlebot", "/login", False),
    ("Googlebot", "/api/search", False),
    # The pages that earn the site its traffic. A file that blocks the trap and
    # also blocks these is not a fix.
    ("Googlebot", "/story/abc", True),
    ("Googlebot", "/fandom/harry-potter", True),
    ("Googlebot", "/ship/draco-malfoy-hermione-granger", True),
    ("Googlebot", "/fandoms", True),
    ("Googlebot", "/ships", True),
    ("Googlebot", "/sitemap.xml", True),
    # Total-disallow groups: training crawlers and SEO auditors.
    ("GPTBot", "/", False),
    ("CCBot", "/story/abc", False),
    ("ClaudeBot", "/fandom/harry-potter", False),
    ("SemrushBot", "/story/abc", False),
    ("AhrefsBot", "/", False),
    ("MJ12bot", "/ship/x", False),
    # Search crawlers that are deliberately NOT blocked.
    ("Applebot", "/story/abc", True),
    ("Amzn-SearchBot", "/fandom/harry-potter", True),
]

# Crawl-delay only reaches a legacy parser if it is inside an intact group.
EXPECTED_CRAWL_DELAY = 10

SITE = "https://ficatlas.com"


def blank_lines_inside_groups(text: str) -> list[tuple[int, str]]:
    """Line numbers of blank lines that fall inside a user-agent group.

    A group is open from a User-agent line until the next User-agent line or a
    non-rule directive at column 0 (Sitemap). Comments do not open or close one.
    """
    bad = []
    in_group = False
    agent = ""
    for n, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            in_group = True
            agent = line.split(":", 1)[1].strip()
            continue
        if low.startswith("sitemap:"):
            in_group = False
            continue
        if line == "":
            # Only a blank that is FOLLOWED by more rules in the same group is a
            # problem; a blank before the next User-agent is the separator. That
            # distinction needs lookahead, so it is resolved by the caller below.
            if in_group:
                bad.append((n, agent))
            continue
    return bad


def main() -> int:
    text = ROBOTS.read_text(encoding="utf-8")
    lines = text.split("\n")
    failures = []

    # A blank inside a group is only harmless if nothing but comments and blanks
    # separate it from the next User-agent line — i.e. it is the group separator.
    for n, agent in blank_lines_inside_groups(text):
        rest = lines[n:]
        for nxt in rest:
            s = nxt.strip()
            if s == "" or s.startswith("#"):
                continue
            if s.lower().startswith(("user-agent:", "sitemap:")):
                break  # separator, fine
            failures.append(
                f"{ROBOTS.name}:{n}: blank line inside the `{agent}` group, with "
                f"`{s}` still to come. A 1994-style parser ends the group here "
                f"and drops every rule below it. Use a comment for spacing."
            )
            break

    rp = RobotFileParser()
    rp.parse(lines)

    if rp.default_entry is None:
        failures.append(
            "the `User-agent: *` group is invisible to a legacy parser — it "
            "produced no rules at all. Almost always a blank line directly "
            "under the `User-agent: *` line."
        )
    else:
        delay = rp.crawl_delay("*")
        if delay != EXPECTED_CRAWL_DELAY:
            failures.append(
                f"Crawl-delay for `*` reads as {delay!r} under a legacy parser, "
                f"expected {EXPECTED_CRAWL_DELAY}. If it was changed on purpose, "
                f"change EXPECTED_CRAWL_DELAY here too."
            )

    for agent, path, want in EXPECTED:
        got = rp.can_fetch(agent, SITE + path)
        if got != want:
            verb = "allowed" if got else "blocked"
            failures.append(
                f"{agent} is {verb} on {path}, expected "
                f"{'allowed' if want else 'blocked'}."
            )

    # Wildcard rules: presence only. See the note in the module docstring for why
    # these cannot be asserted through the strict parser.
    for rule in ("Disallow: /*?", "Disallow: /story/*/chapter/"):
        if not re.search(r"^" + re.escape(rule) + r"$", text, re.M):
            failures.append(
                f"`{rule}` is gone. It is the rule a modern crawler reads to stay "
                f"out of the infinite filter space; a legacy parser never saw it "
                f"anyway, so losing it costs the whole defence."
            )

    if not re.search(r"^Sitemap: https://\S+/sitemap\.xml$", text, re.M):
        failures.append("no absolute `Sitemap:` line — see NEXT_PUBLIC_SITE_URL.")

    if failures:
        print("robots.txt check FAILED\n")
        for f in failures:
            print(f"  - {f}")
        return 1

    n_groups = len(re.findall(r"^User-agent:", text, re.M))
    print(f"robots.txt ok — {n_groups} groups, {len(EXPECTED)} access assertions, "
          f"and the `*` group survives a strict 1994-style parse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
