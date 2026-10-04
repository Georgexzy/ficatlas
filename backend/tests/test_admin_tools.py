"""The control centre's read-only tools: story lookup keys and the activity view."""
import inspect

from api import admin


def test_story_key_reads_every_form_an_operator_pastes():
    assert admin._story_key("https://archiveofourown.org/works/10057010/chapters/1") == ("ao3", "10057010")
    assert admin._story_key("http://www.fanfiction.net/s/3639659/1/") == ("ffnet", "3639659")
    assert admin._story_key("ao3:42") == ("ao3", "42")
    assert admin._story_key("FFNET 7") == ("ffnet", "7")
    assert admin._story_key("harry potter") is None


def test_activity_never_reports_its_own_query():
    # Otherwise the panel's own poll is always the "longest running" row.
    assert "pg_backend_pid()" in inspect.getsource(admin.db_activity)


def test_inspector_compares_columns_with_the_term_lists():
    """The point of the tool: a stale gate column is visible per work."""
    src = inspect.getsource(admin.inspect_story)
    assert "gate_terms.UNDERAGE_TAGS" in src and "gate_terms.ADULT_TAGS" in src
