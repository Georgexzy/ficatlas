"""Trope hubs are public pages with no content toggle whose URLs get shared,
so the tag NAME must be safe, not only the works under it. See topic_hubs.py."""
import topic_hubs as t

NAMES = {"draco malfoy", "harry potter", "hermione granger"}


def test_real_tropes_become_pages():
    for tag in ["Time Travel", "Slow Burn", "Hurt/Comfort", "Found Family",
                "Alternate Universe - Canon Divergence", "Albus Dumbledore Bashing"]:
        assert t.usable_tag(tag, NAMES), tag


def test_nothing_unsafe_or_meaningless_does():
    for tag in ["Smut", "Explicit Sexual Content", "Rape/Non-con Elements",
                "Implied/Referenced Child Abuse", "Underage Sex", "Major Character Death",
                "Dead Dove: Do Not Eat", "First Time", "Omegaverse", "Mpreg",
                "ao3_meta_dump", "reddit_recs", "tropedia_recs", "spoilers: PoA",
                "era: R", "Other Additional Tags to Be Added", "Not Beta Read",
                "Hermione Granger", "Draco Malfoy/Harry Potter", "Harry Potter & Ron Weasley"]:
        assert not t.usable_tag(tag, NAMES), tag
