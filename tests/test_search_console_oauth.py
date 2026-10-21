"""Exercise the local OAuth callback without sending credentials to Google."""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


SPEC = importlib.util.spec_from_file_location(
    "search_console", Path(__file__).resolve().parents[1] / "tools/search_console.py")
gsc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gsc)


class SearchConsoleOAuthTests(TestCase):
    def test_browser_callback_saves_private_read_only_token(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            client_file = root / "client.json"
            token_file = root / "token.json"
            client_file.write_text(json.dumps({"installed": {
                "client_id": "local-test", "client_secret": "local-secret",
                "token_uri": gsc.TOKEN_URL,
            }}))
            client_file.chmod(0o600)
            calls = []
            callbacks = []

            def open_browser(url):
                query = parse_qs(urlparse(url).query)
                self.assertEqual(query["scope"], [gsc.READ_SCOPE])
                self.assertEqual(query["code_challenge_method"], ["S256"])
                redirect = query["redirect_uri"][0]
                callback = redirect + "?code=sample-code&state=" + query["state"][0]
                callbacks.append(Thread(target=lambda: urlopen(callback, timeout=3).read()))
                callbacks[-1].start()

            def exchange(url, fields):
                calls.append((url, fields))
                return {"access_token": "short-lived", "refresh_token": "long-lived",
                        "scope": gsc.READ_SCOPE, "expires_in": 3600}

            with patch.object(gsc.webbrowser, "open", side_effect=open_browser), \
                 patch.object(gsc, "_post_form", side_effect=exchange):
                gsc.authorize(client_file, token_file, gsc.READ_SCOPE)
            for callback in callbacks:
                callback.join(timeout=3)
                self.assertFalse(callback.is_alive())
            saved = json.loads(token_file.read_text())
            self.assertEqual(saved["scope"], gsc.READ_SCOPE)
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(calls[0][1]["code"], "sample-code")
            self.assertTrue(calls[0][1]["code_verifier"])
            with patch.object(gsc, "_post_form") as post:
                self.assertEqual(gsc.access_token(client_file, token_file, gsc.READ_SCOPE),
                                 "short-lived")
                post.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "auth --write"):
                    gsc.access_token(client_file, token_file, gsc.WRITE_SCOPE)

    def test_client_and_token_files_must_be_private(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            path.write_text('{"installed":{"client_id":"x"}}')
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(RuntimeError, "chmod 600"):
                gsc._client(path)
