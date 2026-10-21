"""The edge rules that return 403 must not be able to catch a real crawler.

`deploy/nginx.conf` refuses two things at the origin: a named agent
(`$refused_agent`) and a client that claims Chrome while sending neither
`sec-ch-ua` nor `Sec-Fetch-Mode` (`$forged_chrome`). The second rule is the
important one and the dangerous one — the rotating botnet it exists for has real
browser headers, so the rule has to key on the header contradiction, and every
real crawler that embeds Chrome in its user agent looks exactly like it.

That is not hypothetical. 2026-10-21, 12:30:56 → 12:35:49: the rule was live on
the bind mount without its crawler exemption while it was being iterated, and
bingbot was refused 30 times in five minutes at its crawl delay — 30 of the 498
requests it made that day, on story, series, author, hub and sitemap paths. The
log shows the cutover exactly: every request refused, then every request
passing. A crawler that accumulates refusals stops coming, and on this site
Bing is the only one that arrives at all.

So the exemption is asserted here against the real regex, extracted from the
config file rather than restated — the same reason `check-robots.py` runs the
live file through a parser instead of checking a copy of its rules. A test that
held its own copy of the pattern would keep passing while the config drifted,
which is how the two query parsers came to disagree in §8.

Run: python3 tests/check-edge-rules.py
"""
import re
import sys
from pathlib import Path

CONF = Path(__file__).resolve().parent.parent / "deploy" / "nginx.conf"

# (user agent, sec-ch-ua, sec-fetch-mode, must the request be refused)
CASES = [
    # The botnet: Chrome claimed, no browser metadata. Both headers empty is
    # what the rule matches on, so this is the one it must keep catching.
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36", "", "", True),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "", "", True),
    # A real Chromium that suppressed Client Hints still sends Sec-Fetch-Mode.
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
     '"Chromium";v="151"', "navigate", False),
    # Crawlers that embed Chrome, exactly as bingbot does, and exactly what the
    # 2026-10-21 log shows being refused while the exemption was missing.
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
     "+http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36",
     "", "", False),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html) "
     "Chrome/116.0.0.0 Safari/537.36", "", "", False),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
     "", "", False),
    ("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots) "
     "Chrome/112.0.0.0 Safari/537.36", "", "", False),
    ("Mozilla/5.0 (compatible; DuckDuckBot/1.0; +http://duckduckgo.com/duckduckbot.html) "
     "Chrome/112.0.0.0 Safari/537.36", "", "", False),
    ("Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html) "
     "Chrome/112.0.0.0 Safari/537.36", "", "", False),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.0 Safari/605.1.15", "", "", False),
    # Neither claims Chrome: curl, wget, the health checks, the harvester.
    ("curl/8.5.0", "", "", False),
    ("python-httpx/0.27.0", "", "", False),
]

# Agents the exemption has to carry. Checked separately from the cases so a name
# missing from BOTH the config and this list cannot slip through unnoticed.
EXEMPT_AGENTS = ["Googlebot", "bingbot", "DuckDuckBot", "YandexBot",
                 "Baiduspider", "Applebot"]


def forged_chrome_regex(conf: str) -> re.Pattern:
    """The single rule inside the `$forged_chrome` map, as Python sees it."""
    block = re.search(
        r'map "\$http_user_agent\|\$http_sec_ch_ua\|\$http_sec_fetch_mode" '
        r'\$forged_chrome \{(.*?)\n\s*\}', conf, re.S)
    if not block:
        raise SystemExit(
            "no `$forged_chrome` map found in deploy/nginx.conf — if the rule "
            "was renamed or re-keyed, this check has to follow it, because the "
            "exemption it protects is the only thing between a crawler and a 403.")
    rules = re.findall(r'~\*(\S+)', block.group(1))
    if len(rules) != 1:
        raise SystemExit(f"expected exactly one regex rule in the map, got {rules}")
    # `~*` is nginx for "case-insensitive"; nginx anchors are PCRE and these
    # patterns stay inside what Python's `re` and PCRE agree on.
    return re.compile(rules[0], re.I)


def main() -> int:
    conf = CONF.read_text()
    pattern = forged_chrome_regex(conf)
    failures = []

    for ua, ch, fm, want in CASES:
        got = bool(pattern.match(f"{ua}|{ch}|{fm}"))
        if got != want:
            label = "refused" if got else "let through"
            failures.append(f"  - {label} but should not be: {ua[:70]}…")

    for agent in EXEMPT_AGENTS:
        if agent.lower() not in pattern.pattern.lower():
            failures.append(
                f"  - `{agent}` is no longer exempt from the forged-browser rule. "
                f"It embeds Chrome in its agent string, so it will be refused on "
                f"every request until it is back in the lookahead.")

    if failures:
        print("edge-rules check FAILED\n")
        for f in failures:
            print(f)
        return 1

    print(f"edge rules ok — {len(CASES)} agent/header combinations agree with "
          f"deploy/nginx.conf, and {len(EXEMPT_AGENTS)} crawlers stay exempt "
          f"from the forged-browser 403.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
