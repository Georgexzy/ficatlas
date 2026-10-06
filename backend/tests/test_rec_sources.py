"""rec_sources: who recommended which work, and what is NOT a recommendation."""
import rec_sources as rs


def _blurb(work, users):
    lis = "".join(f'''
        <li class="user short blurb group user-1" role="article">
          <div class="header module"><h5 class="byline heading">Bookmarked by
            <a href="/users/{u}/pseuds/{u}">{u}</a></h5>
            <p class="datetime">{d}</p>
            <p class="status">{'<span class="rec" title="Rec"></span>' if rec else ''}</p>
          </div></li>''' for u, d, rec in users)
    return f'''<li id="bookmark_{work}" class="bookmark blurb group work-{work}" role="article">
      <h4 class="heading"><a href="/works/{work}">A Title</a></h4>
      <p class="datetime">12 Jun 2021</p>
      <div class="recent module group"><ul class="bookmark index group">{lis}</ul></div></li>'''


def test_parses_each_rec_bookmark_with_its_own_date_not_the_works():
    page = "<ol>" + _blurb(111, [("anna", "06 Oct 2026", True), ("bo", "20 Sep 2026", True)]) \
        + _blurb(222, [("cy", "05 Oct 2026", True)]) + "</ol>"
    rows = rs.parse_rec_page(page)
    assert [(r["who"], r["site_id"], r["when"].strftime("%Y-%m-%d")) for r in rows] == [
        ("anna", "111", "2026-10-06"), ("bo", "111", "2026-09-20"), ("cy", "222", "2026-10-05")]


def test_a_bookmark_not_ticked_rec_is_not_a_recommendation():
    page = _blurb(111, [("anna", "06 Oct 2026", False), ("bo", "06 Oct 2026", True)])
    assert [r["who"] for r in rs.parse_rec_page(page)] == ["bo"]


def test_ao3_tag_escaping():
    assert rs.ao3_tag_id("Harry Potter - J. K. Rowling") == "Harry Potter - J*d* K*d* Rowling"
    assert rs.ao3_tag_id("Sherlock & Co") == "Sherlock *a* Co"
    assert rs.ao3_tag_id("A/B") == "A*s*B"


def test_self_promotion_is_never_a_recommendation():
    for t in ["self-rec time!", "Fic Rec of the Day: His Beloved by Me",
              "new chapter is up", "Chapter 12 is up now", "I wrote a thing, rec welcome",
              "an old favourite fic of mine", "No new fic today from me, but rec",
              "just posted! #ficrec", "Cross posted to AO3"]:
        assert not rs.is_recommendation(t, need_rec_word=True), t


def test_genuine_recommendations_survive():
    for t in ["Popping in late to rec You'll Always Be My Favorite Ghost by someone",
              "Fic rec Friday #97! i'd rather take my whiskey neat",
              "Go read this, it wrecked me", "FIC REC: Make Way for Ducklings"]:
        assert rs.is_recommendation(t, need_rec_word=True), t


def test_bluesky_reads_links_from_facets_not_the_shortened_text():
    post = {"uri": "at://x/app.bsky.feed.post/1", "author": {"handle": "Reader.bsky.social"},
            "record": {"text": "fic rec! archiveofourown.org/works/53...",
                       "createdAt": "2026-10-06T10:00:00Z",
                       "facets": [{"features": [{"uri": "https://archiveofourown.org/works/53218846"}]}]}}
    ms = rs.bsky_mentions([post])
    assert [(m["site"], m["site_id"], m["who"]) for m in ms] == [
        ("ao3", "53218846", "bsky:reader.bsky.social")]


def test_bluesky_link_only_in_text_is_ignored():
    post = {"uri": "at://x/1", "author": {"handle": "r"},
            "record": {"text": "rec archiveofourown.org/works/53...", "createdAt": "2026-10-06T10:00:00Z"}}
    assert rs.bsky_mentions([post]) == []


def test_tumblr_counts_original_posts_and_not_reblogs():
    content = [{"type": "text", "text": "my favourite rec",
                "formatting": [{"type": "link", "url": "https://archiveofourown.org/works/42"}]}]
    original = {"id": 1, "blogName": "Alice", "postUrl": "u", "timestamp": 1790000000,
                "content": content, "trail": []}
    reblog = dict(original, id=2, blogName="bob", trail=[{"blog": {"name": "alice"}}])
    ms = rs.tumblr_mentions([original, reblog])
    assert [(m["who"], m["site_id"]) for m in ms] == [("tumblr:alice", "42")]
