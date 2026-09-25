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


def test_a_phrase_teaches_its_distinctive_words_not_its_grammar():
    """`_hinted_tags` looks up single lowercase words and sums their lift, so a
    phrase teaches several words pointing at one tag. Most of a sentence is
    grammar and can carry no mapping."""
    got = T._phrase_words("they pretend to be dating for the whole story")
    assert "pretend" in got and "dating" in got
    for w in ("the", "they", "for", "story"):
        assert w not in got


def test_one_post_is_an_anecdote(db):
    """"he loses his memory and she finds him" would otherwise teach
    `finds` -> Amnesia for ever, on the strength of a single post."""
    from sqlalchemy import text as sql_text
    import json
    db.execute(sql_text("DELETE FROM post_labels WHERE post_id LIKE 'probe%'"))
    db.execute(sql_text(
        "INSERT INTO post_labels (post_id, raw, grounded, model) "
        "VALUES ('probe1', '{}'::jsonb, CAST(:g AS jsonb), 'test')"),
        {"g": json.dumps({"wants": [{"phrase": "amnesia plot", "tag": "Amnesia"}],
                          "excludes": []})})
    db.commit()
    before = db.execute(sql_text(
        "SELECT count(*) FROM tag_hints WHERE tag = 'Amnesia' "
        "AND word = 'amnesia'")).scalar()
    T.teach_hints(db, dry_run=False)
    after = db.execute(sql_text(
        "SELECT count(*) FROM tag_hints WHERE tag = 'Amnesia' "
        "AND word = 'amnesia'")).scalar()
    assert T.MIN_SUPPORT >= 2
    assert after == before, "a single post taught a mapping"
    db.execute(sql_text("DELETE FROM post_labels WHERE post_id LIKE 'probe%'"))
    db.commit()


def test_a_taught_word_cannot_place_a_tag_alone():
    """The mined hints carry a statistical lift and this is not that number.
    One word from one post must be worth less than the floor `_hinted_tags`
    applies to summed lift, or a label becomes a veto on everything else."""
    from api.search import _HINT_MIN_SCORE
    assert T.TAUGHT_LIFT < _HINT_MIN_SCORE, (
        f"a single taught word ({T.TAUGHT_LIFT}) clears the hint floor "
        f"({_HINT_MIN_SCORE}) by itself")


def test_a_name_teaches_nothing_about_a_trope(db):
    """The first teach produced `harry -> Canon Divergence`, `steve ->
    Hurt/Comfort` and `harry -> Alternate Universe`. A name appears in every
    phrase about that character, so it correlates with whatever trope the post
    happened to want -- and the extractor already resolves names properly, so a
    hint on one only adds noise to a term it would have found anyway."""
    import inspect
    src = inspect.getsource(T.teach_hints)
    assert "own_words" in src
    # Per post, not against tag_hints' global name list: that holds 46,181
    # words including ordinary English, and filtering by it removed `canon` and
    # `brothers` along with `harry` and taught nothing at all.
    assert "_name_words" not in src


def test_the_teacher_is_still_out_of_every_request_path():
    """Restated after --teach was added, because that is the step which finally
    writes into a table the extractor reads. What crosses over is DATA, in a
    table api/search already consulted; no request imports this module."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("api/search.py", "main.py", "worker.py"):
        assert "extractor_teacher" not in (root / name).read_text()
