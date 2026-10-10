"""Search-triggered AO3 discovery stays on robots-permitted fandom pages."""
import asyncio

from live_fetch.ao3_live import _build_ao3_url, fetch_live_ao3


def test_fandom_scoped_description_uses_tag_listing():
    url = _build_ao3_url({
        "q": "Moody trains the Marauders",
        "fandoms": "Harry Potter",
        "sort": "relevance",
    })
    assert url.startswith("https://archiveofourown.org/tags/Harry%20Potter/works?")
    assert "work_search%5Bquery%5D=Moody+trains+the+Marauders" in url
    assert "/works/search?" not in url


def test_automated_free_text_without_fandom_is_skipped(monkeypatch):
    called = []

    async def unexpected_fetch(*args, **kwargs):
        called.append(True)
        return []

    monkeypatch.setattr("live_fetch.ao3_live._fetch_page", unexpected_fetch)
    result = asyncio.run(fetch_live_ao3(
        {"q": "Moody trains the Marauders"}, automated=True))
    assert result == []
    assert called == []
