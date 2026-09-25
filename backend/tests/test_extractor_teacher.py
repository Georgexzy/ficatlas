"""The teacher proposes; the index disposes.

Everything here guards the boundary that makes an offline model acceptable in
this codebase: nothing it invents may reach the extractor, and nothing at
request time may depend on it.
"""
import extractor_teacher as T


def test_the_teacher_is_never_in_a_request_path():
    """The whole design. When the teaching is done this script stops being run
    and NOTHING changes, because no request path touches it."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("api/search.py", "main.py", "worker.py"):
        src = (root / name).read_text()
        assert "extractor_teacher" not in src, \
            f"{name} imports the teacher — it would become a dependency"


def test_an_account_level_refusal_stops_the_run(monkeypatch):
    """A bare HTTPStatusError on every post reads as "the teacher is broken".
    The first real run said exactly that four times over when the true answer
    was one sentence from the API: the key was valid and the account had no
    credit."""
    class R:
        status_code = 400
        @staticmethod
        def json():
            return {"error": {"message": "Your credit balance is too low"}}

    class Client:
        def post(self, *a, **k):
            return R()

    import pytest
    with pytest.raises(T.TeacherRefused) as e:
        T.label_post(Client(), "k", "title", "body")
    assert "credit balance" in str(e.value)


def test_a_fenced_reply_is_still_a_label(monkeypatch):
    """The model is asked for bare JSON and usually obliges; a fenced block is
    the one deviation worth tolerating rather than discarding the call."""
    class R:
        status_code = 200
        @staticmethod
        def json():
            return {"content": [{"text": '```json\n{"fandom": "X"}\n```'}]}

    class Client:
        def post(self, *a, **k):
            return R()

    assert T.label_post(Client(), "k", "t", "b") == {"fandom": "X"}


def test_an_invented_tag_never_reaches_the_extractor(db):
    """The model is good at reading a paragraph and has no idea what this
    archive calls things. A name it makes up resolves to nothing and is
    dropped, which is what keeps a hallucination out of the vocabulary."""
    raw = {"fandom": "Definitely Not A Real Fandom Xyzzy",
           "wants": [{"phrase": "they pretend to date",
                      "idea": "Completely Invented Trope Xyzzy"}],
           "excludes": [], "characters": []}
    out = T._ground(db, raw)
    assert "fandom" not in out
    assert out["wants"] == []
    # And the drop is recorded, because a want we cannot name is a gap in the
    # vocabulary rather than noise.
    assert any(d["name"].startswith("Completely Invented") for d in out["dropped"])


def test_a_want_is_kept_with_the_phrase_that_justified_it(db):
    """The pairing IS the lesson -- a phrase-to-tag mapping of exactly the kind
    tag_hints could not mine statistically."""
    from sqlalchemy import text as sql_text
    db.execute(sql_text(
        "INSERT INTO facets (kind, value, count, norm) "
        "VALUES ('tag', 'Fake/Pretend Relationship', 90000, 'fakepretend') "
        "ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count"))
    db.commit()
    out = T._ground(db, {"wants": [{"phrase": "they pretend to be dating",
                                    "idea": "Fake/Pretend Relationship"}],
                         "excludes": [], "characters": []})
    assert out["wants"] == [{"phrase": "they pretend to be dating",
                             "tag": "Fake/Pretend Relationship",
                             "works": 90000}]


def test_a_want_with_no_phrase_is_worthless(db):
    """An entry without the words that justify it teaches nothing and cannot
    be checked against the post."""
    out = T._ground(db, {"wants": [{"phrase": "", "idea": "Fluff"}],
                         "excludes": [], "characters": []})
    assert out["wants"] == []


def test_the_similarity_operator_actually_runs(db):
    """`%%` reaches Postgres verbatim from text() -- SQLAlchemy does no
    %-formatting there -- and fails with "operator does not exist: text %%
    unknown". _resolve swallows lookup errors, so the doubled operator made
    EVERY proposal resolve to nothing and every want vanish silently, which is
    indistinguishable from a model that said nothing useful."""
    import inspect
    src = inspect.getsource(T._resolve)
    assert "value %% :n" not in src
    assert "value % :n" in src


def test_both_label_sources_go_through_the_same_door():
    """The API is one way to get a model to read these posts and not the only
    one. What must not vary is what happens next -- a second import path that
    trusted its input would be the hole through which a hallucinated tag
    reaches the extractor."""
    import inspect
    src = inspect.getsource(T.load_labels)
    assert "_ground(db, raw)" in src, \
        "imported labels must be grounded exactly as API labels are"
