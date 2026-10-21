#!/usr/bin/env python3
"""Local Google Search Console OAuth and API helper for FicAtlas.

Run ``python3 tools/search_console.py --help``. OAuth credentials and tokens
live under the user's home directory, never in the repository or site runtime.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser


READ_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_CREDENTIALS = Path.home() / ".config/ficatlas/search-console/client_secret.json"
DEFAULT_TOKEN = Path.home() / ".local/share/ficatlas/search-console/token.json"
DEFAULT_PROPERTY = "sc-domain:ficatlas.com"


def _read_private_json(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"Missing {path}. See docs/search-console-oauth.md")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"{path} must be private: chmod 600 {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _client(path: Path) -> dict:
    data = _read_private_json(path)
    client = data.get("installed")
    if not isinstance(client, dict) or not client.get("client_id"):
        raise RuntimeError("Use a Google OAuth client of type Desktop app")
    if client.get("token_uri", TOKEN_URL) != TOKEN_URL:
        raise RuntimeError("Unexpected OAuth token endpoint in client file")
    return client


def _save_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _post_form(url: str, fields: dict) -> dict:
    req = Request(url, data=urlencode(fields).encode(),
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _request_json(req)


def _request_json(req: Request) -> dict:
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read(8_000_000)
            return json.loads(body) if body else {}
    except HTTPError as exc:
        try:
            error = json.load(exc)
            detail = error.get("error_description") or error.get("error", {}).get("message")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(f"Google returned HTTP {exc.code}: {detail or exc.reason}") from None
    except URLError as exc:
        raise RuntimeError(f"Cannot reach Google: {exc.reason}") from None


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _grants(token: dict, needed: str) -> bool:
    scopes = set(token.get("scope", "").split())
    return needed in scopes or (needed == READ_SCOPE and WRITE_SCOPE in scopes)


def _authorization_url(client_id: str, redirect_uri: str, scope: str,
                       state: str, verifier: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
    })


def authorize(credentials: Path, token_path: Path, scope: str,
              port: int = 0, open_browser: bool = True,
              force: bool = False) -> None:
    client = _client(credentials)
    if token_path.exists() and not force:
        old = _read_private_json(token_path)
        if old.get("client_id") == client["client_id"] and _grants(old, scope):
            print("Already authorized. Use --reauthorize to sign in again.")
            return

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    received: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass  # Never log the one-time authorization code.

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/start":
                self.send_response(302)
                self.send_header("Location", url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path != "/":
                self.send_error(404)
                return
            values = parse_qs(urlparse(self.path).query)
            if values.get("state", [""])[0] != state:
                self.send_error(400, "Invalid OAuth state")
                return
            received["code"] = values.get("code", [""])[0]
            received["error"] = values.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Search Console authorization received. You can close this tab.")

    with HTTPServer(("127.0.0.1", port), Callback) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/"
        url = _authorization_url(client["client_id"], redirect_uri, scope,
                                 state, verifier)
        start_url = f"http://127.0.0.1:{server.server_port}/start"
        print("Open this short link in a browser on this computer:")
        print(start_url, flush=True)
        print("Open this Google authorization URL in a browser on this computer:")
        print(url, flush=True)
        if open_browser:
            webbrowser.open(start_url)
        deadline = time.monotonic() + 300
        while not received and time.monotonic() < deadline:
            server.timeout = min(1, max(0.1, deadline - time.monotonic()))
            server.handle_request()
    if received.get("error"):
        raise RuntimeError(f"Google authorization declined: {received['error']}")
    if not received.get("code"):
        raise RuntimeError("Authorization timed out after five minutes")

    token = _post_form(TOKEN_URL, {
        "code": received["code"],
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if not token.get("refresh_token") or not _grants(token, scope):
        raise RuntimeError("Google did not grant offline access and the requested scope")
    token["client_id"] = client["client_id"]
    token["expires_at"] = time.time() + int(token.get("expires_in", 0))
    _save_private_json(token_path, token)
    print("Authorized. Token saved privately outside the repository.")


def access_token(credentials: Path, token_path: Path, scope: str) -> str:
    client = _client(credentials)
    token = _read_private_json(token_path)
    if token.get("client_id") != client["client_id"]:
        raise RuntimeError("Token belongs to a different OAuth client; run auth --reauthorize")
    if not _grants(token, scope):
        suffix = " --write" if scope == WRITE_SCOPE else ""
        raise RuntimeError(f"Missing required scope; run auth{suffix} --reauthorize")
    if token.get("access_token") and token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    fresh = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    })
    if not fresh.get("access_token"):
        raise RuntimeError("Google did not refresh the token; run auth --reauthorize")
    if fresh.get("scope") and not _grants(fresh, scope):
        raise RuntimeError("Refreshed token lacks the required scope")
    token.update(fresh)
    token["expires_at"] = time.time() + int(fresh.get("expires_in", 0))
    _save_private_json(token_path, token)
    return token["access_token"]


def api(token: str, url: str, method: str = "GET", body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    return _request_json(Request(url, data=data, headers=headers, method=method))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--property", default=DEFAULT_PROPERTY,
                        help="Search Console property (default: sc-domain:ficatlas.com)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show local connection state without contacting Google")
    auth = commands.add_parser("auth", help="Authorize in your browser")
    auth.add_argument("--write", action="store_true", help="Request Search Console write scope")
    auth.add_argument("--reauthorize", action="store_true")
    auth.add_argument("--port", type=int, default=0, help="Local callback port; useful over SSH")
    auth.add_argument("--no-browser", action="store_true")
    commands.add_parser("properties", help="List accessible properties")
    commands.add_parser("sitemaps", help="List submitted sitemaps")
    inspect = commands.add_parser("inspect", help="Inspect one URL's Google index status")
    inspect.add_argument("url")
    perf = commands.add_parser("performance", help="Fetch recent search performance")
    perf.add_argument("--days", type=int, default=28)
    perf.add_argument("--dimension", choices=("query", "page", "date", "country", "device"),
                      default="query")
    perf.add_argument("--limit", type=int, default=250)
    submit = commands.add_parser("submit-sitemap", help="Submit a FicAtlas sitemap")
    submit.add_argument("url", nargs="?", default="https://ficatlas.com/sitemap.xml")
    submit.add_argument("--apply", action="store_true", help="Actually submit")
    args = parser.parse_args()

    if args.command == "status":
        print("OAuth client:", "present" if args.credentials.is_file() else "missing")
        if args.token.is_file():
            token = _read_private_json(args.token)
            print("Token: present")
            print("Scope:", "write" if _grants(token, WRITE_SCOPE) else
                  "read-only" if _grants(token, READ_SCOPE) else "unknown")
        else:
            print("Token: missing")
        return 0
    if args.command == "auth":
        authorize(args.credentials, args.token,
                  WRITE_SCOPE if args.write else READ_SCOPE,
                  args.port, not args.no_browser, args.reauthorize)
        return 0

    scope = WRITE_SCOPE if args.command == "submit-sitemap" else READ_SCOPE
    if args.command == "submit-sitemap":
        target = urlparse(args.url)
        if target.scheme != "https" or target.hostname != "ficatlas.com" or target.query:
            raise RuntimeError("Only HTTPS ficatlas.com sitemap URLs can be submitted")
        if not args.apply:
            print(f"Would submit {args.url} for {args.property}. Add --apply to submit.")
            return 0
    token = access_token(args.credentials, args.token, scope)
    site = quote(args.property, safe="")
    root = f"https://www.googleapis.com/webmasters/v3/sites/{site}"
    if args.command == "properties":
        result = api(token, "https://www.googleapis.com/webmasters/v3/sites")
    elif args.command == "sitemaps":
        result = api(token, f"{root}/sitemaps")
    elif args.command == "inspect":
        parsed = urlparse(args.url)
        if parsed.scheme != "https" or parsed.hostname != "ficatlas.com":
            raise RuntimeError("Only HTTPS ficatlas.com URLs can be inspected")
        result = api(token, "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                     "POST", {"inspectionUrl": args.url, "siteUrl": args.property,
                              "languageCode": "en-GB"})
    elif args.command == "performance":
        if not 1 <= args.days <= 480 or not 1 <= args.limit <= 25000:
            raise RuntimeError("--days must be 1..480 and --limit 1..25000")
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        result = api(token, f"{root}/searchAnalytics/query", "POST", {
            "startDate": (end - timedelta(days=args.days - 1)).isoformat(),
            "endDate": end.isoformat(), "dimensions": [args.dimension],
            "rowLimit": args.limit,
        })
    else:
        result = api(token, f"{root}/sitemaps/{quote(args.url, safe='')}", "PUT")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"Search Console: {exc}", file=sys.stderr)
        sys.exit(1)
