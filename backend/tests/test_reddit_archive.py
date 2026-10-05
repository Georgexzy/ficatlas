"""Reddit feeds served from the archives must parse exactly as Reddit's did.

The three consumers are untouched and parse Reddit's Atom layout; this pins the
shape reddit_archive renders, using the consumers' own parsers, so a change to
either side that breaks the other fails here rather than as a week of silence.
"""
import reddit_archive
import rec_threads
import reddit_answers

POST = {"id": "abc123", "subreddit": "HPfanfiction", "author": "asker",
        "created_utc": 1790000000, "title": "Looking for long Drarry & slow burn",
        "selftext": "Something like https://archiveofourown.org/works/111 please",
        "permalink": "/r/HPfanfiction/comments/abc123/looking_for/"}
COMMENT = {"id": "c1", "author": "helper", "created_utc": 1790000500,
           "body": "Try [this](https://archiveofourown.org/works/222) & https://www.fanfiction.net/s/333/1/ <3",
           "permalink": "/r/HPfanfiction/comments/abc123/_/c1/"}


def _fake(monkeypatch):
    def get_json(url):
        if "/posts/ids" in url:
            return {"data": [POST]}
        if "/comments/search" in url:
            return {"data": [COMMENT]}
        return {"data": [POST]}
    monkeypatch.setattr(reddit_archive, "_get_json", get_json)
    monkeypatch.setattr(reddit_archive, "ENABLED", True)


def test_a_search_feed_yields_threads_the_consumer_can_read(monkeypatch):
    _fake(monkeypatch)
    xml = reddit_archive.atom_for(rec_threads.SEARCH.format(sub="HPfanfiction", q='flair%3A%22Request%22'))
    entries = rec_threads._ENTRY.findall(xml)
    assert len(entries) == 1
    href = rec_threads._HREF.search(entries[0]).group(1)
    assert rec_threads._POSTID.search(href).group(1) == "abc123"
    assert rec_threads._field(entries[0], "title") == "Looking for long Drarry &amp; slow burn"


def test_comment_links_survive_the_double_escape(monkeypatch):
    _fake(monkeypatch)
    xml = reddit_archive.atom_for(rec_threads.THREAD.format(sub="HPfanfiction", pid="abc123"))
    ms = rec_threads.mentions_in(xml)
    assert {(m["site"], m["site_id"]) for m in ms} >= {("ao3", "222"), ("ffnet", "333")}
    bodies = [e["body"] for e in reddit_answers._entries(xml)]
    assert any("& https://www.fanfiction.net/s/333/1/ <3" in b for b in bodies)


def test_unknown_urls_fall_back_to_reddit(monkeypatch):
    _fake(monkeypatch)
    assert reddit_archive.atom_for("https://www.reddit.com/user/someone.rss") is None
    assert reddit_archive.atom_for("https://example.com/r/x/new.rss") is None


def test_a_requesters_own_links_are_not_recommendations(monkeypatch):
    # The post links work 111 as "something like this"; only the helper's
    # reply is a recommendation.
    _fake(monkeypatch)
    xml = reddit_archive.atom_for(rec_threads.THREAD.format(sub="HPfanfiction", pid="abc123"))
    every = {(m["site"], m["site_id"]) for m in rec_threads.mentions_in(xml)}
    replies = {(m["site"], m["site_id"]) for m in rec_threads.mentions_in(xml, skip_op=True)}
    assert ("ao3", "111") in every and ("ao3", "111") not in replies
    assert replies == {("ao3", "222"), ("ffnet", "333")}


def test_every_community_query_is_one_the_archive_reader_understands():
    # A free-text query goes to the months-stale archive; a flair query must be
    # exactly the shape reddit_archive parses, or it silently becomes text.
    for c in rec_threads.COMMUNITIES:
        assert c["kind"] in ("thread", "request", "recs")
        if c["query"].startswith("flair:"):
            assert reddit_archive._FLAIR.match(c["query"]), c
