"""Reader searches observed in the Audience report in October 2026."""
import pytest
from fastapi import HTTPException

from api.library import _canonical_story_url
from api.search import _pasted_work, _repeated_named_fandom


def test_ao3_link_followed_by_a_filter_keeps_both_parts():
    site, work_id, filters = _pasted_work(
        "https://archiveofourown.org/works/77535806 cat:m/m")
    assert site.value == "ao3"
    assert work_id == "77535806"
    assert filters == "cat:m/m"
    assert _pasted_work("https://archiveofourown.org/works/77535806 find similar") is None


def test_import_uses_only_the_work_url_and_checks_its_host():
    assert _canonical_story_url(
        "https://archiveofourown.org/works/77535806/chapters/1 cat:m/m"
    ) == "https://archiveofourown.org/works/77535806"
    assert _canonical_story_url(
        "http://m.fanfiction.net/s/14316442/4/Title?ref=search"
    ) == "https://www.fanfiction.net/s/14316442/1/"
    with pytest.raises(HTTPException) as error:
        _canonical_story_url("https://example.com/?next=archiveofourown.org/works/77535806")
    assert error.value.status_code == 400


def test_repeated_fandom_name_outweighs_loose_character_matches():
    assert _repeated_named_fandom(
        "alejandro knows Diego is zorro and keeps the secret of Zorro", "Zorro")
    assert not _repeated_named_fandom(
        "a simple day out was all Bumblebee wanted", "Wanted (2008)")
