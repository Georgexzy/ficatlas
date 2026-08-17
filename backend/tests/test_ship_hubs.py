"""Ship slugs and pairing collapsing.

Three things here can go wrong silently, and all three are visible to readers
rather than only to a crawler:

  * pairing order. "A/B" and "B/A" are the same ship and must land on one page.
    Getting it wrong splits 49,047 Drarry works across a strong page and two
    thin ones, which is duplicate content aimed straight at a crawler.
  * romantic vs platonic. AO3's "/" and " & " mean different things to the
    people reading, and both slugify identically — so a rule that lets platonic
    tags through merges a ship with a friendship on one URL.
  * the display name. It feeds the page's search link, and search resolves a
    facet term by substring against the vocabulary. Show the rare spelling and
    the link resolves against the 1,541-work variant while the 47,460-work one
    sits next to it.
"""
import pytest

from ship_hubs import _collapse, ship_key


class TestShipKey:
    def test_pairing_order_does_not_change_the_slug(self):
        assert ship_key("Draco Malfoy/Harry Potter") \
            == ship_key("Harry Potter/Draco Malfoy")

    def test_stray_whitespace_does_not_change_the_slug(self):
        """A real facet row: 'Harry Potter/ Draco Malfoy', 46 works."""
        assert ship_key("Harry Potter/ Draco Malfoy") \
            == ship_key("Draco Malfoy/Harry Potter")

    def test_case_does_not_change_the_slug(self):
        assert ship_key("harry potter/DRACO MALFOY") \
            == ship_key("Draco Malfoy/Harry Potter")

    def test_slug_is_alphabetical_not_as_written(self):
        assert ship_key("Harry Potter/Draco Malfoy") == "draco-malfoy-harry-potter"

    def test_the_x_separator_is_romantic_too(self):
        assert ship_key("Draco Malfoy x Harry Potter") \
            == ship_key("Draco Malfoy/Harry Potter")

    def test_platonic_pairings_get_no_hub(self):
        """'&' means friendship, and it slugifies identically to '/'. Building
        both would put a ship and a gen fic on the same URL."""
        assert ship_key("Draco Malfoy & Harry Potter") is None
        assert ship_key("Draco Malfoy&Harry Potter") is None

    def test_mixed_separators_are_skipped(self):
        assert ship_key("Draco Malfoy/Harry Potter & Ron Weasley") is None

    def test_non_pairings_are_rejected(self):
        """The third largest relationship facet in the index is 49,223 works of
        'Minor or Background Relationship(s)', which is not a ship."""
        assert ship_key("Minor or Background Relationship(s)") is None
        assert ship_key("Other Relationship Tags to Be Added") is None
        assert ship_key("") is None
        assert ship_key("   ") is None

    def test_threesomes_are_kept_and_ordered(self):
        assert ship_key("Draco Malfoy/Harry Potter/Severus Snape") \
            == ship_key("Severus Snape/Harry Potter/Draco Malfoy")

    def test_more_than_three_halves_is_dropped(self):
        """Long enough that the slug is unusable and rare enough not to matter."""
        assert ship_key("A Person/B Person/C Person/D Person") is None

    def test_an_empty_half_is_not_a_pairing(self):
        assert ship_key("Harry Potter/") is None
        assert ship_key("/Harry Potter") is None

    def test_unsluggable_names_yield_none(self):
        """Callers skip these rather than creating a hub at /ship/."""
        assert ship_key("???/???") is None


class TestCollapse:
    def test_both_orders_become_one_hub(self):
        hubs = _collapse([
            ("Draco Malfoy/Harry Potter", 47460),
            ("Harry Potter/Draco Malfoy", 1541),
            ("Harry Potter/ Draco Malfoy", 46),
        ])
        assert list(hubs) == ["draco-malfoy-harry-potter"]
        assert len(hubs["draco-malfoy-harry-potter"]["variants"]) == 3

    def test_display_name_is_the_most_used_spelling(self):
        """Not the alphabetical one. 'Sherlock Holmes/John Watson' is how 60,810
        works are tagged; sorting it would put a spelling on the page that
        almost nobody writes, and hand search the wrong term to resolve."""
        hubs = _collapse([
            ("John Watson/Sherlock Holmes", 900),
            ("Sherlock Holmes/John Watson", 60810),
        ])
        hub = hubs["john-watson-sherlock-holmes"]
        assert hub["name"] == "Sherlock Holmes/John Watson"

    def test_display_name_is_whitespace_normalised(self):
        """The stray space in the real 'Harry Potter/ Draco Malfoy' facet row
        must not reach the page heading or the search link built from it."""
        hubs = _collapse([("Harry  Potter/ Draco Malfoy", 10)])
        assert hubs["draco-malfoy-harry-potter"]["name"] == "Harry Potter/Draco Malfoy"

    def test_platonic_rows_do_not_join_the_romantic_hub(self):
        hubs = _collapse([
            ("Draco Malfoy/Harry Potter", 47460),
            ("Draco Malfoy & Harry Potter", 2939),
        ])
        assert list(hubs) == ["draco-malfoy-harry-potter"]
        assert hubs["draco-malfoy-harry-potter"]["variants"] \
            == ["Draco Malfoy/Harry Potter"]

    def test_counts_sum_across_variants(self):
        hubs = _collapse([
            ("Draco Malfoy/Harry Potter", 47460),
            ("Harry Potter/Draco Malfoy", 1541),
        ])
        assert hubs["draco-malfoy-harry-potter"]["approx"] == 49001

    def test_ordering_of_input_does_not_change_the_result(self):
        rows = [
            ("Harry Potter/Draco Malfoy", 1541),
            ("Draco Malfoy/Harry Potter", 47460),
            ("Castiel/Dean Winchester", 83582),
        ]
        a = _collapse(rows)
        b = _collapse(list(reversed(rows)))
        assert {k: (v["name"], sorted(v["variants"]), v["approx"]) for k, v in a.items()} \
            == {k: (v["name"], sorted(v["variants"]), v["approx"]) for k, v in b.items()}

    def test_a_tie_does_not_depend_on_input_order(self):
        rows = [("A Person/B Person", 100), ("B Person/A Person", 100)]
        assert _collapse(rows)["a-person-b-person"]["name"] \
            == _collapse(list(reversed(rows)))["a-person-b-person"]["name"]
