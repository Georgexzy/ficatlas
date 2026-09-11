"""Who counts as a reader.

The traffic panel exists so an operator can tell whether anybody is using the
site. Every request misclassified as human is a number that says yes when the
answer is no, and the failure is silent — nothing about an inflated visitor
count looks wrong.
"""
import pytest

from tracking import is_bot


# Real browsers, which must never be filtered out. If one of these starts
# reading as a bot the panel under-reports and the mistake is invisible in the
# other direction.
HUMAN = [
    ("desktop Chrome", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
    ("Windows Chrome", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
    ("Firefox",        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
                       "Gecko/20100101 Firefox/130.0"),
    ("iPhone Safari",  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
                       "Mobile/15E148 Safari/604.1"),
    ("Android Chrome", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/152.0.0.0 Mobile Safari/537.36"),
    ("desktop Safari", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
]

AUTOMATION = [
    # The one that actually got through, and cost the most. A test script in
    # this repo used urllib's default user agent; the regex named
    # `python-requests` and nothing else, so 173 scripted searches were counted
    # as an audience — and because the visitor hash is per day per IP per agent,
    # each run also read as a new visitor.
    ("urllib default",   "Python-urllib/3.12"),
    ("requests",         "python-requests/2.31.0"),
    ("httpx",            "python-httpx/0.27.0"),
    ("curl",             "curl/8.5.0"),
    ("wget",             "Wget/1.21.4"),
    ("playwright",       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) HeadlessChrome/131.0.6778.33 Safari/537.36"),
    ("node-fetch",       "node-fetch/1.0"),
    ("Go client",        "Go-http-client/2.0"),
    ("Java",             "Java/17.0.9"),
    ("Postman",          "PostmanRuntime/7.37.0"),
    ("Googlebot",        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("Applebot",         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1)"),
]


@pytest.mark.parametrize("label,ua", HUMAN, ids=[h[0] for h in HUMAN])
def test_real_browsers_are_counted_as_people(label, ua):
    assert is_bot(ua) is False


@pytest.mark.parametrize("label,ua", AUTOMATION, ids=[a[0] for a in AUTOMATION])
def test_automation_is_not_counted_as_people(label, ua):
    assert is_bot(ua) is True


@pytest.mark.parametrize("ua", ["", "   ", None])
def test_a_missing_user_agent_is_automation(ua):
    """Every real browser sends one; it is not optional in practice. Treating
    "unknown" as human is how an audience figure drifts upwards, and it made the
    laziest possible client the one most likely to be counted."""
    assert is_bot(ua) is True
