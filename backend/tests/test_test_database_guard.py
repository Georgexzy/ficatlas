"""Private app sessions must inherit the test DB selected by conftest."""
import os
from urllib.parse import urlsplit

import pytest


def test_database_url_is_pinned_to_isolated_test_database():
    test_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    assert urlsplit(test_url).path.rsplit("/", 1)[-1].endswith("_test")
    assert os.environ.get("DATABASE_URL") == test_url
