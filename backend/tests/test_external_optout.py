"""Unit tests for external_optout.has_external_optout.

The detector is deliberately conservative: removing a work is destructive, so
it must catch explicit "don't put my work on other sites" notices while never
firing on the common, non-refusal ways summaries talk about other sites.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from external_optout import has_external_optout


# ── Genuine opt-outs (must be True) ──────────────────────────────────────────
def test_do_not_repost():
    assert has_external_optout("PLEASE DON'T REPOST MY WORKS ON ANY OTHER SITES.")


def test_do_not_repost_to_other_sites():
    assert has_external_optout("DO NOT REPOST TO ANY OTHER SITES WITHOUT MY PERMISSION")


def test_do_not_repost_without_permission():
    assert has_external_optout("Do not repost my works without my express permission")


def test_repost_embedded_in_notes_block():
    assert has_external_optout("*** DO NOT COPY / DO NOT REPOST *** The voices promised him fire.")


def test_do_not_upload_other_site():
    assert has_external_optout("Do not upload to any other site.")


def test_do_not_copy_work_another_site():
    assert has_external_optout("Do not copy my work on another site without my permission")


def test_no_permission_to_repost():
    assert has_external_optout("you do NOT have permission to repost or upload my work")


def test_do_not_post_on_goodreads():
    assert has_external_optout("PLEASE DON'T POST MY FICS ON GOODREADS/STORYGRAPHS/ETC.")


def test_atyd_sirius_perspective_summary():
    s = ("this is an ATYD fanfic first!!! all credit goes to MsKingBean89. "
         "PLEASE DON'T REPOST MY WORKS ON ANY OTHER SITES. DO NOT SELL BOUND COPIES.")
    assert has_external_optout(s)


# ── False positives (must be False) ─────────────────────────────────────────
def test_posted_on_another_site_too():
    # Telling readers it is ALSO cross-posted is not a refusal.
    assert not has_external_optout("This is posted on other sites, but they are my own accounts.")


def test_post_mean_reviews():
    assert not has_external_optout("Please don't post mean reviews, only encouraging ones.")


def test_licensed_to_translate_and_redistribute():
    # A grant of permission, not a refusal.
    assert not has_external_optout("Licensed to translate and redistribute")


def test_dont_post_reviews():
    assert not has_external_optout("If you don't like it don't read it. Don't post harsh reviews.")


def test_challenge_on_another_site():
    assert not has_external_optout("This was a challenge on another site. It was fun to write.")


def test_redistribute_in_game_mechanics():
    # "redistribute" used as plot, no directive.
    assert not has_external_optout("Their powers are shuffled and redistributed.")


def test_do_not_post_after_wine():
    assert not has_external_optout("don't post after a few glasses of wine.")


def test_do_not_post_chapters():
    assert not has_external_optout("Sorry if I don't post for a while.")


def test_post_reviews_on_slap_page():
    # A fictional in-story instruction, not an author opt-out.
    assert not has_external_optout('Do not post on your Slap page that I\'m here.')


def test_do_not_post_reviews_on_goodreads():
    # Telling readers not to post REVIEWS is not a no-repost notice.
    assert not has_external_optout("PLEASE DO NOT POST REVIEWS ON GOODREADS. I do this as a hobby.")


def test_may_or_may_not_repost():
    # Speculative ("may or may not repost eventually") is not a refusal.
    assert not has_external_optout("The beginning is awful. May or may not repost eventually.")


def test_empty_and_none():
    assert not has_external_optout(None)
    assert not has_external_optout("")
