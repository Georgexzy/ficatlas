"""Every email is HTML and text from one layout. See email_templates.py."""
import email_templates as t

WORKS = [("a1", "The <Arithmancer> & Co", "White Squirrel", "ffnet", 112, 2)]


def test_story_titles_are_escaped():
    # Titles are written by strangers; one carrying markup must stay text.
    _, text, html, _ = t.digest_email(username="g", works=WORKS, token="T")
    assert "&lt;Arithmancer&gt; &amp; Co" in html
    assert "<Arithmancer>" not in html
    assert "The <Arithmancer> & Co" in text           # plain text is plain


def test_a_digest_carries_both_unsubscribe_routes():
    _, text, html, headers = t.digest_email(username="g", works=WORKS, token="TOK")
    assert "/unsubscribe#t=TOK" in html and "/unsubscribe#t=TOK" in text
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "/api/follows/email/unsubscribe?t=TOK" in headers["List-Unsubscribe"]


def test_a_full_digest_is_not_clipped_by_gmail():
    # Gmail clips messages over ~102KB, hiding the footer and its unsubscribe.
    works = [(f"id{i}", f"A fairly long fanfiction title number {i}", "Some Author",
              "ao3", 40 + i, 1 + i % 3) for i in range(25)]
    _, _, html, _ = t.digest_email(username="g", works=works, token="T")
    assert len(html.encode()) < 60_000


def test_reset_names_the_account_and_keeps_the_code_usable():
    subject, text, html = t.reset_email(username="george", link="https://x/reset#code=abc",
                                        code="abc-123", ttl_min=60)
    assert subject == "Reset your FicAtlas password"
    for body in (text, html):
        assert "george" in body and "abc-123" in body and "60 minutes" in body
    assert "https://x/reset#code=abc" in html


def test_every_email_has_a_preheader_and_alt_text():
    _, _, html = t.reset_email(username="g", link=None, code="c", ttl_min=60)
    assert 'display:none' in html and 'alt=""' in html and 'lang="en"' in html
