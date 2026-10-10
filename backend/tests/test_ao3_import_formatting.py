from bs4 import BeautifulSoup

from ao3_import import _chapter_content
from html_sanitize import sanitize_html, tidy_chapter_html


def test_ao3_paragraph_markup_survives_direct_import():
    userstuff = BeautifulSoup(
        '<div class="userstuff"><p>First paragraph.</p>'
        '<p>Second <em>emphasized</em> paragraph.</p></div>',
        "lxml",
    ).select_one("div.userstuff")
    content = _chapter_content(userstuff)
    assert "<p>First paragraph.</p>" in content
    assert "<p>Second <em>emphasized</em> paragraph.</p>" in content
    served = tidy_chapter_html(sanitize_html(content))
    assert "<p>First paragraph.</p>" in served
    assert "<p>Second <em>emphasized</em> paragraph.</p>" in served


def test_unwrapped_ao3_text_is_converted_to_paragraphs():
    userstuff = BeautifulSoup(
        '<div class="userstuff">First paragraph.\n\nSecond paragraph.</div>',
        "lxml",
    ).select_one("div.userstuff")
    content = _chapter_content(userstuff)
    assert content == "<p>First paragraph.</p><p>Second paragraph.</p>"
