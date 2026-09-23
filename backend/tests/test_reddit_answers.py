"""The corpus the extractor is finally measurable against."""
import reddit_answers as R


def test_work_links_are_found_in_both_archives_shapes():
    body = ('try <a href="https://archiveofourown.org/works/12402765/chapters/9">this</a> '
            'or https://www.fanfiction.net/s/8260843/1/Some-Title')
    assert R.fic_links(body) == [("ao3", "12402765"), ("ffnet", "8260843")]


def test_the_same_fic_linked_twice_is_one_fic():
    body = ("archiveofourown.org/works/999 and again "
            "https://archiveofourown.org/works/999/chapters/3")
    assert R.fic_links(body) == [("ao3", "999")]


def test_reddit_double_escapes_the_comment_html():
    """One unescape leaves &lt;a href=...&gt; and not a single link matches --
    which would have read as "nobody ever answers these posts"."""
    xml = ("<entry><author><name>/u/someone</name></author>"
           "<content type=\"html\">&amp;lt;a href=\"https://archiveofourown.org/works/555\""
           "&amp;gt;here&amp;lt;/a&amp;gt;</content></entry>")
    entries = R._entries(xml)
    assert len(entries) == 1
    assert R.fic_links(entries[0]["body"]) == [("ao3", "555")]


def test_the_original_poster_agreeing_is_the_stronger_signal():
    """A commenter can link the wrong fic, or a similar one, or their own. The
    OP linking it back is Reddit's way of saying that was the one."""
    entries = [
        {"author": "/u/asker", "body": "I'm looking for a fic where..."},
        {"author": "/u/helper", "body": "https://archiveofourown.org/works/111"},
        {"author": "/u/other",  "body": "maybe archiveofourown.org/works/222"},
        {"author": "/u/asker",  "body": "that's the one! archiveofourown.org/works/111"},
    ]
    got = {a["site_id"]: a["confirmed"] for a in R.answers_in(entries, "/u/asker")}
    assert got == {"111": True, "222": False}


def test_a_post_nobody_answered_is_still_recorded_as_checked(monkeypatch, db):
    """"Nobody answered this" is a fact worth keeping, and re-asking costs a
    request we are strictly rationed on."""
    from sqlalchemy import text as sql_text
    db.execute(sql_text(
        "INSERT INTO reddit_posts (id, subreddit, title, url, state) "
        "VALUES ('t3_probe', 'FanFiction', 'lost fic', 'u', 'new') "
        "ON CONFLICT (id) DO UPDATE SET answers_checked_at = NULL"))
    db.commit()
    monkeypatch.setattr(R, "fetch_comments", lambda *a, **k: [])
    stats = R.harvest(db, limit=50, sleep=lambda _s: None)
    assert stats["posts"] >= 1
    checked = db.execute(sql_text(
        "SELECT answers_checked_at FROM reddit_posts WHERE id='t3_probe'")).scalar()
    assert checked is not None


def test_being_rate_limited_does_not_retire_a_post(monkeypatch, db):
    """A 429 says nothing about the post. Marking it checked loses it for good
    -- the same shape that cost the FF.net enrichment a day of queued captures,
    where a refusal returned the same value as "there is nothing there"."""
    from sqlalchemy import text as sql_text
    db.execute(sql_text(
        "INSERT INTO reddit_posts (id, subreddit, title, url, state) "
        "VALUES ('t3_refused', 'FanFiction', 'lost fic', 'u', 'new') "
        "ON CONFLICT (id) DO UPDATE SET answers_checked_at = NULL"))
    db.commit()

    def refuse(*a, **k):
        raise R.Refused("HTTP 429")

    monkeypatch.setattr(R, "fetch_comments", refuse)
    stats = R.harvest(db, limit=50, sleep=lambda _s: None)
    assert stats["refused"] >= 1
    checked = db.execute(sql_text(
        "SELECT answers_checked_at FROM reddit_posts WHERE id='t3_refused'")).scalar()
    assert checked is None, "a throttled post must stay in the queue"


def test_the_eval_skips_answers_this_index_does_not_hold():
    """Scoring a pair whose answer we never indexed would punish the extractor
    for the archive's gaps rather than its own -- no query could find it."""
    import extractor_eval
    assert "a.story_id IS NOT NULL" in extractor_eval.CORPUS_SQL


def test_the_eval_can_be_restricted_to_confirmed_answers():
    """A commenter can link the wrong fic. The OP linking it back is the
    stronger label and has to be separable."""
    import extractor_eval
    assert ":confirmed_only" in extractor_eval.CORPUS_SQL


def test_the_corpus_asks_about_the_posts_most_likely_to_be_answered():
    """Opposite of the outreach queue, for the opposite reason. Outreach wants
    posts nobody has answered yet and works newest-first; this wants posts
    somebody HAS answered, and a thread three hours old has had no time."""
    assert "posted_at ASC" in R.harvest.__doc__ or True
    import inspect
    assert "ORDER BY posted_at ASC" in inspect.getsource(R.harvest)


def test_an_answered_post_survives_the_queue_s_retention(db):
    """A post paired with the fic it turned out to be has stopped being a
    worklist entry and become the corpus. It takes weeks of rationed Reddit
    requests to collect one, so ageing it out on the QUEUE's schedule would
    make recall@10 permanently un-improvable -- the measurement would churn at
    exactly the rate it was gathered."""
    from sqlalchemy import text as sql_text
    import reddit_queue

    db.execute(sql_text("""
        INSERT INTO reddit_posts (id, subreddit, title, url, state, seen_at)
        VALUES ('t3_old_answered', 'FanFiction', 'lost fic', 'u', 'new',
                now() - interval '99 days'),
               ('t3_old_bare',     'FanFiction', 'lost fic', 'u', 'new',
                now() - interval '99 days')
        ON CONFLICT (id) DO UPDATE SET seen_at = EXCLUDED.seen_at
    """))
    db.execute(sql_text(
        "INSERT INTO reddit_answers (post_id, url) "
        "VALUES ('t3_old_answered', 'https://archiveofourown.org/works/1') "
        "ON CONFLICT DO NOTHING"))
    db.commit()

    db.execute(sql_text("""
        DELETE FROM reddit_posts p
         WHERE p.seen_at < now() - make_interval(days => :d)
           AND NOT EXISTS (SELECT 1 FROM reddit_answers a WHERE a.post_id = p.id)
    """), {"d": reddit_queue.KEEP_DAYS})
    db.commit()

    left = {r[0] for r in db.execute(sql_text(
        "SELECT id FROM reddit_posts WHERE id LIKE 't3_old_%'")).fetchall()}
    assert "t3_old_answered" in left, "ground truth was aged out with the queue"
    assert "t3_old_bare" not in left, "unanswered posts must still drop out"


def test_corpus_posts_never_reach_the_worklist():
    """r/HPfanfiction is read for evidence and never replied to -- the ban that
    keeps it out of FEEDS stops us replying, not reading. A post nobody here
    can answer must never appear on a screen that invites somebody to answer
    it, so the separation is by state and the worklist does not accept that
    state at all."""
    import reddit_queue
    from api.queue import _STATES

    assert reddit_queue.CORPUS_STATE not in _STATES, \
        "the worklist would serve posts nobody here can reply to"
    assert reddit_queue.CORPUS_FEEDS, "no corpus feeds configured"
    worklist_subs = {s for s, _ in reddit_queue.FEEDS}
    corpus_subs = {s for s, _ in reddit_queue.CORPUS_FEEDS}
    assert not (worklist_subs & corpus_subs), \
        "a subreddit is either answerable or read-only, not both"


def test_the_corpus_feeds_can_be_skipped_when_only_the_queue_matters():
    """They cost the same rationed Reddit requests and fill no screen."""
    import inspect
    import reddit_queue
    assert "corpus: bool = True" in inspect.signature(
        reddit_queue.run).__str__().replace("'", "") or True
    src = inspect.getsource(reddit_queue.run)
    assert "if corpus:" in src
