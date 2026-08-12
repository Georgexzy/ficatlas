"""Every exit from search() must declare what a shared cache may do with it.

The Cache-Control header used to be set once, at the bottom of search(). But
search() has three exits, and the other two are the cache hits: an L1 hit and a
shared-tier hit both `return` long before the bottom is reached.

So the responses that carried the header were exactly the ones computed from
scratch, and the responses that did not were the repeated, popular queries —
the only ones an edge cache could ever have helped with. Cloudflare kept the
first slow answer and marked every fast one DYNAMIC. The shared-cache design
defeated itself at precisely the point it was supposed to pay off, and it looked
fine from the outside: 200s throughout, correct bodies, nothing in the logs.

Two rules are defended here:

  1. the header says `public` for anonymous readers and `private, no-store` for
     signed-in ones — an operator's results include delisted works and must
     never be handed to somebody else by a shared proxy;
  2. no `return` inside search() skips it.

The second is checked against the source with the ast module rather than over
HTTP, because the failure was never in what search() computed. It was in which
line it left by, and that is a property of the function's shape.
"""
import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import SEARCH_CACHE_SECONDS, _apply_cache_headers, search  # noqa: E402


class _FakeResponse:
    """Just the headers dict — that is the whole of the contract used here."""

    def __init__(self):
        self.headers = {}


class _FakeViewer:
    pass


def test_anonymous_reader_gets_a_shared_cacheable_header():
    r = _FakeResponse()
    _apply_cache_headers(r, None)
    assert r.headers["Cache-Control"] == (
        f"public, max-age={SEARCH_CACHE_SECONDS}, "
        f"stale-while-revalidate={SEARCH_CACHE_SECONDS * 4}")


def test_signed_in_viewer_is_never_shared():
    r = _FakeResponse()
    _apply_cache_headers(r, _FakeViewer())
    cc = r.headers["Cache-Control"]
    assert cc == "private, no-store"
    # The point of the branch: a shared cache must not keep this.
    assert "public" not in cc


def test_missing_response_object_is_tolerated():
    # `response: Response = None` in the signature, so None is reachable when the
    # endpoint is called directly rather than through FastAPI.
    _apply_cache_headers(None, None)  # must not raise


def _search_function_node():
    src = inspect.getsource(search)
    # getsource on a decorated function keeps the decorator, which is not valid
    # as a module body on its own indentation level — dedent by parsing it as the
    # single statement it is.
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    return fn


def _is_apply_call(node) -> bool:
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_apply_cache_headers")


def test_no_return_in_search_skips_the_cache_header():
    """Walk every block in search(); a `return` must be preceded by the call.

    Nested helper functions defined inside search() are skipped — their returns
    are not endpoint exits.
    """
    fn = _search_function_node()

    offenders = []

    def walk(body, inside_nested=False):
        for i, stmt in enumerate(body):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk(getattr(stmt, "body", []), inside_nested=True)
                continue
            if isinstance(stmt, ast.Return) and not inside_nested:
                # A bare `return` (no value) exits without producing a response
                # body, so there is nothing for a cache to keep.
                if stmt.value is None:
                    continue
                prev = body[i - 1] if i > 0 else None
                if not _is_apply_call(prev):
                    offenders.append(stmt.lineno)
                continue
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list):
                    walk(inner, inside_nested)
            for handler in getattr(stmt, "handlers", []):
                walk(handler.body, inside_nested)

    walk(fn.body)

    assert not offenders, (
        "search() returns a response without calling _apply_cache_headers first, "
        f"at line(s) {offenders} of the function. Every exit must declare its "
        "cacheability — the cache-hit paths are the ones that matter most.")


def test_the_guard_can_actually_fail():
    """A guard that cannot fail is not a guard.

    Confirms the walker flags an unprotected return rather than passing
    vacuously, which is the way a source-level check quietly rots.
    """
    src = (
        "def f():\n"
        "    if x:\n"
        "        return early\n"
        "    _apply_cache_headers(response, viewer)\n"
        "    return late\n"
    )
    fn = ast.parse(src).body[0]
    found = []

    def walk(body):
        for i, stmt in enumerate(body):
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                prev = body[i - 1] if i > 0 else None
                if not _is_apply_call(prev):
                    found.append(stmt.lineno)
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list):
                    walk(inner)

    walk(fn.body)
    assert found == [3]
