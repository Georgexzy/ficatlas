"""Hub slugs and variant collapsing.

The two things here that can silently go wrong:

  * a slug that is not stable or not unique, which either 404s a page that used
    to work or merges two unrelated fandoms onto one URL;
  * variant collapsing, which is the whole reason hubs are not simply one per
    facet row — get it wrong and "Harry Potter" and "Harry Potter - J. K.
    Rowling" become two hubs listing overlapping works, which is duplicate
    content pointed straight at a crawler.
"""
import pytest

from fandom_hubs import _collapse, slugify


class TestSlugify:
    def test_basic(self):
        assert slugify("Harry Potter") == "harry-potter"

    def test_case_and_spacing_do_not_change_the_slug(self):
        assert slugify("  HARRY   POTTER  ") == slugify("Harry Potter")

    def test_punctuation_collapses_rather_than_doubling_separators(self):
        # "Marvel Cinematic Universe (MCU)" must not become "...universe--mcu-"
        assert slugify("Marvel Cinematic Universe (MCU)") == "marvel-cinematic-universe-mcu"

    def test_accents_fold_instead_of_vanishing(self):
        """The bug this guards: stripping non-ascii outright turns Pokémon into
        'pokmon'. Folding keeps it readable and unifies it with 'Pokemon'."""
        assert slugify("Pokémon") == "pokemon"
        assert slugify("Pokémon") == slugify("Pokemon")

    def test_ampersands_and_slashes(self):
        assert slugify("Steven Universe & Adventure Time") == "steven-universe-adventure-time"
        assert slugify("Buffy/Angel") == "buffy-angel"

    def test_no_leading_or_trailing_separators(self):
        s = slugify("!!! Danger Days !!!")
        assert not s.startswith("-") and not s.endswith("-")

    def test_a_name_with_nothing_sluggable_yields_empty(self):
        """Callers skip these rather than creating a hub at /fandom/."""
        assert slugify("???") == ""
        assert slugify("") == ""


class TestCollapse:
    def test_author_suffix_variants_become_one_hub(self):
        hubs = _collapse([
            ("Harry Potter", 686558),
            ("Harry Potter - J. K. Rowling", 381225),
        ])
        assert list(hubs) == ["harry-potter"]
        hub = hubs["harry-potter"]
        assert sorted(hub["variants"]) == ["Harry Potter", "Harry Potter - J. K. Rowling"]

    def test_display_name_prefers_the_shorter_form(self):
        hubs = _collapse([
            ("Harry Potter - J. K. Rowling", 381225),
            ("Harry Potter", 686558),
        ])
        assert hubs["harry-potter"]["name"] == "Harry Potter"

    def test_distinct_works_are_not_merged(self):
        """fandom_base drops an author suffix, not a subtitle. Cursed Child is a
        different work from Harry Potter and must keep its own hub."""
        hubs = _collapse([
            ("Harry Potter", 686558),
            ("Harry Potter and the Cursed Child - Thorne & Rowling", 4200),
        ])
        assert len(hubs) == 2
        assert "harry-potter-and-the-cursed-child" in hubs

    def test_unsluggable_names_are_dropped_not_grouped_under_empty(self):
        hubs = _collapse([("???", 500), ("Naruto", 456030)])
        assert list(hubs) == ["naruto"]

    def test_counts_sum_across_variants(self):
        hubs = _collapse([("Naruto", 456030), ("Naruto - Kishimoto", 1000)])
        assert hubs["naruto"]["approx"] == 457030

    def test_ordering_of_input_does_not_change_the_result(self):
        rows = [("Naruto", 10), ("Harry Potter", 20), ("Harry Potter - J. K. Rowling", 5)]
        a = _collapse(rows)
        b = _collapse(list(reversed(rows)))
        assert {k: (v["name"], sorted(v["variants"]), v["approx"]) for k, v in a.items()} \
            == {k: (v["name"], sorted(v["variants"]), v["approx"]) for k, v in b.items()}
