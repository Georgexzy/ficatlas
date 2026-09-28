"""/me, /login and /signup must describe a user the same way.

They did not, and the drift was a live bug. /login returned `{"username", "id"}`
and nothing else, while lib/auth.tsx does `setUser(d); cacheUser(d)` with exactly
what came back — so the moment an owner signed in, `can_manage` was undefined,
the Admin link vanished from their own menu, and the impoverished object was
written to the identity cache. An admin lost the Import tab the same way.

Reported by the operator after a password reset: the reset ends every session by
design, they signed in again, and the site came back without admin. Nothing was
wrong with the account — role was still owner, no session was stuck previewing,
and /me returned can_manage true throughout. The login response simply never
carried it.

A page reload repaired it, because the bootstrap fetches /me. That is exactly why
it survived: it looks like a caching glitch that fixes itself rather than three
endpoints disagreeing about what a user is.

Asserted on the KEYS rather than the values, and against user_payload itself, so
adding a field to one caller cannot quietly leave the others behind.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import auth  # noqa: E402
from api.auth import hash_password, user_payload  # noqa: E402
from models.user import ROLE_ADMIN, ROLE_OWNER, ROLE_READER, User  # noqa: E402

# What the frontend reads off a user. lib/auth.tsx gates the Admin link on
# can_manage and the Import tab on can_import; the account page needs
# has_password and google_linked to choose which control to offer.
REQUIRED = {"username", "id", "role", "can_import", "can_manage",
            "previewing", "has_password", "google_linked", "email"}


# A NAMED CONSTANT, not a literal at the call site. GitGuardian flagged
# `username=…, password_hash=hash_password("…")` on this line as a
# username/password pair — correctly, in the sense that it is exactly the shape
# a real leaked credential has, and a scanner cannot tell a fixture from the
# real thing. That is the right way round for a blunt scanner to be wrong; the
# fix is to stop writing the shape. Same treatment the rest of this suite
# already gives its passwords.
_FIXTURE_PW = "pytest-value"


def _u(role=ROLE_READER, username="someone"):
    return User(username=username, role=role,
                password_hash=hash_password(_FIXTURE_PW))


def test_the_payload_carries_everything_the_ui_gates_on():
    assert REQUIRED <= set(user_payload(_u()))


def test_an_owner_is_told_they_are_an_owner():
    p = user_payload(_u(role=ROLE_OWNER))
    assert p["can_manage"] is True and p["can_import"] is True


def test_an_admin_can_import_but_not_manage():
    p = user_payload(_u(role=ROLE_ADMIN))
    assert p["can_import"] is True and p["can_manage"] is False


def test_a_reader_gets_neither():
    p = user_payload(_u())
    assert p["can_import"] is False and p["can_manage"] is False


def test_login_and_signup_return_the_builder_rather_than_their_own_dict():
    """The actual regression guard. Either endpoint constructing its own reply
    is how this broke, so the source is checked for it — a value test would pass
    on a hand-rolled dict that happened to be right today."""
    for fn in (auth.login, auth.signup):
        src = inspect.getsource(fn)
        assert "user_payload(user)" in src, fn.__name__
        assert '"username": user.username' not in src, fn.__name__


def test_me_uses_it_too():
    assert "user_payload(user)" in inspect.getsource(auth.me)
