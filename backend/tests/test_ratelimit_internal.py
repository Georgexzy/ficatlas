"""The render-token exemption.

Both halves matter and they pull in opposite directions:

  * without an exemption, every server-rendered page counts against one bucket
    (they all arrive from the frontend container), so the whole site caps at
    RATE_READ renders a minute regardless of hardware;
  * with too broad an exemption — trusting the source address, say — proxied
    browser traffic is exempt too, because that also reaches the backend from
    the frontend container. That is every request, and the limiter stops
    existing.

So the token is the discriminator, and these tests hold both edges: our own
rendering is exempt, and nothing a browser can send is.
"""
import importlib

import pytest


def _reload(monkeypatch, token):
    """ratelimit reads its config at import time."""
    if token is None:
        monkeypatch.delenv("INTERNAL_RENDER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("INTERNAL_RENDER_TOKEN", token)
    import ratelimit
    return importlib.reload(ratelimit)


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


class TestInternalRenderToken:
    def test_matching_token_is_exempt(self, monkeypatch):
        rl = _reload(monkeypatch, "s3cret-token")
        assert rl.is_internal_render(_Req({"x-internal-render": "s3cret-token"}))

    def test_wrong_token_is_not_exempt(self, monkeypatch):
        rl = _reload(monkeypatch, "s3cret-token")
        assert not rl.is_internal_render(_Req({"x-internal-render": "guess"}))

    def test_absent_header_is_not_exempt(self, monkeypatch):
        """Proxied browser traffic. It reaches the backend from the same
        container as our rendering does, and must still be limited."""
        rl = _reload(monkeypatch, "s3cret-token")
        assert not rl.is_internal_render(_Req({}))

    def test_empty_header_is_not_exempt(self, monkeypatch):
        rl = _reload(monkeypatch, "s3cret-token")
        assert not rl.is_internal_render(_Req({"x-internal-render": ""}))

    def test_unconfigured_exempts_nobody(self, monkeypatch):
        """The safe direction. With no token set, an empty header must not
        match an empty secret and hand every caller an exemption."""
        rl = _reload(monkeypatch, None)
        assert not rl.is_internal_render(_Req({"x-internal-render": ""}))
        assert not rl.is_internal_render(_Req({}))
        assert not rl.is_internal_render(_Req({"x-internal-render": "anything"}))

    def test_blank_token_is_treated_as_unconfigured(self, monkeypatch):
        rl = _reload(monkeypatch, "   ")
        assert not rl.is_internal_render(_Req({"x-internal-render": "   "}))

    def test_source_address_grants_nothing(self, monkeypatch):
        """The rejected design: exempting private sources would exempt proxied
        browser traffic too, since that shares the frontend's address."""
        rl = _reload(monkeypatch, "s3cret-token")
        assert not hasattr(rl, "is_internal")


class TestPathClasses:
    """The exemption must not have disturbed which bucket a path lands in."""

    def test_search_is_its_own_class(self, monkeypatch):
        rl = _reload(monkeypatch, "t")
        assert rl.path_class("/api/search") == "search"
        assert rl.path_class("/api/search/random") == "search"

    def test_auth_and_takedown_share_the_tight_bucket(self, monkeypatch):
        rl = _reload(monkeypatch, "t")
        assert rl.path_class("/api/auth/login") == "auth"
        assert rl.path_class("/api/takedown") == "auth"

    def test_revoking_consent_is_never_the_throttled_path(self, monkeypatch):
        rl = _reload(monkeypatch, "t")
        assert rl.path_class("/api/permissions/revoke") == "read"

    def test_everything_else_reads(self, monkeypatch):
        rl = _reload(monkeypatch, "t")
        assert rl.path_class("/api/stories/abc") == "read"
        assert rl.path_class("/api/hubs") == "read"
