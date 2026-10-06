"""author_hubs: which names may become a public /author/ page."""
import author_hubs as ah

NAMES = {"harry potter", "draco malfoy"}


def test_real_usernames_are_usable():
    for n in ["Lomonaaeren", "icarus_chained", "Takara_Phoenix", "TheMultitudesOfMe (x)"]:
        assert ah.usable_name(n, NAMES), n


def test_placeholders_and_names_are_not():
    for n in ["orphan_account", "Anonymous", "Harry Potter", "", "x"]:
        assert not ah.usable_name(n, NAMES), n


def test_unsafe_names_never_become_urls():
    assert not ah.usable_name("crimsonfucker05", NAMES)


def test_ordinary_names_containing_rude_substrings_survive():
    for n in ["Cumberbatch_fan", "EssexGirl", "grapes_of_wrath", "torpedo_bay", "Dickens1812",
              "scrapes", "Sussex"]:
        assert ah.usable_name(n, NAMES), n
