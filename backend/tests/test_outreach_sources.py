"""An outreach source must collect ASKS, not offers. Cases are real posts
from the first dry runs (2026-10-05)."""
import outreach_sources as o

ASKS = [
    "Does anyone have any #StaticMoth fic recommendations?",
    "pls somebody recommend me a fic where a character finally rests",
    "Hello I am looking for a fic! It is actually a Stucky + Sam so I hope you can help",
    "Any fanfic recs with bottom alastor?",
    "Fic rec request - I have some travel coming up and looking to pad the queue, any recs?",
]
NOT_ASKS = [
    "not me looking for fanfic fodder lmao",
    "my bf recommend me a fic and i was praising the author because holy",
    "edit: found!! https://archiveofourown.org/works/84119056",
    "Sharing in case anyone else is looking for fic recs to fit the season",
    "do you need fic recs?! I have fic recs!",
    "Hey! If you're looking for fanfic/writing commissions, pls check out my page",
    "my recs: https://archiveofourown.org/works/1 and https://archiveofourown.org/works/2 ?",
]


def test_asks_are_kept():
    for t in ASKS:
        assert o.looks_like_request(t), t


def test_offers_reports_and_solved_posts_are_not():
    for t in NOT_ASKS:
        assert not o.looks_like_request(t), t
