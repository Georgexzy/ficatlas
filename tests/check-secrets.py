#!/usr/bin/env python3
"""
Refuse to let a credential reach the repository.

    python3 tests/check-secrets.py            # the whole tracked tree
    python3 tests/check-secrets.py --staged   # what is about to be committed

Run by `.githooks/pre-commit`, so on a clone that has done

    git config core.hooksPath .githooks

this runs on every commit and the failure arrives before the push rather than
as a scanner's email afterwards.

Two checks, and the first is the one that matters.

**Nothing from `.env` may appear in a tracked file.** This is exact-value
matching against the secrets this installation actually holds, so it cannot be
fooled by an unfamiliar format, and it catches the realistic accident: a value
pasted into a script "just to test", or a compose file filled in rather than
templated. It needs `.env` to be present, so it is a no-op on a fresh clone —
which is correct, because a clone has no secrets to leak.

**No credential-shaped literal in source.** A DSN carrying a password, a
`password="…"` argument, a private key block, or a provider token. These were
never the live credential — `postgresql://ficatlas:ficatlas@…` was a repeated
development default — but a scanner cannot tell the difference, and neither can
a reader deciding what to paste over. `backend/db/dsn.py` exists so there is
somewhere for the answer to live instead.

The known-and-intended exceptions are listed in ALLOWED below, each with the
reason it is not a leak. Add to it only when you can write that sentence.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Paths that are never worth scanning: vendored dependencies (13MB of Playwright
# whose bundled test fixtures contain JWT-shaped strings), build output, and
# this file, which necessarily contains every pattern it looks for.
SKIP_DIRS = ("node_modules/", ".next/", "backend/data/", "backups/", "__pycache__/")
SKIP_FILES = ("tests/check-secrets.py",)

# (path, substring) pairs that match a rule but are not leaks.
ALLOWED = [
    # IndexNow requires the key to be served publicly at the site root — it
    # authenticates the domain by being fetchable, so publishing it is the
    # entire mechanism, not an accident. It lives in .env only so the backend
    # and the file cannot drift apart.
    ("frontend/public/", "INDEXNOW_KEY"),

    # The documented development default, expressed as a shell fallback rather
    # than a value. The live password comes from .env and has never been in the
    # tree; .env.example says as much in the POSTGRES_PASSWORD comment.
    ("docker-compose.yml", "${POSTGRES_PASSWORD:-ficatlas}"),

    # Instructions telling a reader to substitute their own password, and the
    # note recording which literal was removed from the tree and why.
    ("CLAUDE.md", "<pw>"),
    (".env.example", ""),
    ("IMPROVEMENTS.md", ""),
]

RULES = [
    ("database URL with a password",
     re.compile(r"""(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://
                    [A-Za-z0-9_.-]+ : (?!\$\{|\$[A-Z]|<|%s|\{\{)[^\s:@/'"]{3,} @""",
                re.X)),
    ("password= literal",
     # The exclusion sits inside the quotes: PGPASSWORD="$USER" is a variable
     # being passed, not a password being written down.
     re.compile(r"""(?:password|passwd|pgpassword)\s*[=:]\s*
                    ['"] (?!\$|<|%s|\{\{) [^'"\n]{3,} ['"]""", re.X | re.I)),
    ("private key block",
     re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("opaque token",
     # A Cloudflare tunnel token is a 180-character eyJ… blob and is the most
     # damaging thing in this project's .env — it grants control of the tunnel.
     # It has no provider prefix to key on, so match the shape, and match a
     # long opaque value assigned to a *_TOKEN / *_KEY / *_SECRET name.
     re.compile(r"""eyJ[A-Za-z0-9_\-=]{60,}
                    | (?:TOKEN|KEY|SECRET|PASSWORD)\s*[=:]\s*
                      ['"]? (?!\$|<|%s|\{\{)
                      # not a snake_case identifier: KEY = "ffnet_wayback_cdx_resume"
                      # is a cache-key name, and those are long.
                      (?![a-z0-9]+(?:_[a-z0-9]+)+\b)
                      [A-Za-z0-9+/=_\-]{24,}""", re.X)),

    ("provider token",
     re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
                r"|sk-[A-Za-z0-9\-_]{24,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}")),
]


def tracked_files(staged):
    if staged:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    else:
        cmd = ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout
    for name in out.split("\n"):
        if not name or any(d in name for d in SKIP_DIRS) or name in SKIP_FILES:
            continue
        yield name


def content(name, staged):
    """The version being committed, which is not necessarily the one on disk."""
    if staged:
        r = subprocess.run(["git", "show", f":{name}"], cwd=ROOT,
                           capture_output=True)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    p = ROOT / name
    try:
        return p.read_text("utf-8", "replace")
    except (OSError, UnicodeError):
        return ""


def allowed(name, label, line=""):
    """An exception matches on the path plus either the rule or the actual line.

    Matching the rule alone is too coarse for a documentation file: CLAUDE.md
    has to be able to quote the credential literal this change removed without
    being exempted from credentials generally.
    """
    return any(part in name and (key == "" or key in label or key in line)
               for part, key in ALLOWED)


def env_values():
    """Live secret values, longest first so the report names the specific one."""
    env = ROOT / ".env"
    if not env.exists():
        return []
    out = []
    for line in env.read_text("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        # Short values are words, not secrets: SIGNUP_MODE=open would match
        # every line of prose containing it.
        if len(value) >= 12:
            out.append((key.strip(), value))
    return sorted(out, key=lambda kv: -len(kv[1]))


def main():
    staged = "--staged" in sys.argv
    files = list(tracked_files(staged))
    secrets = env_values()
    problems = []

    if (ROOT / ".env").exists():
        r = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"],
                           cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            problems.append((".env", 0, "tracked", ".env is committed"))

    for name in files:
        text = content(name, staged)
        if not text or "\x00" in text[:2000]:
            continue
        lines = text.split("\n")

        for key, value in secrets:
            if value in text and not allowed(name, key):
                n = next((i for i, l in enumerate(lines, 1) if value in l), 0)
                problems.append((name, n, "live secret",
                                 f"the value of {key} from .env appears here"))
                break

        for label, rx in RULES:
            for i, line in enumerate(lines, 1):
                if rx.search(line) and not allowed(name, label, line):
                    problems.append((name, i, label, line.strip()[:90]))
                    break

    scope = "staged changes" if staged else "tracked files"
    if problems:
        print(f"\n{len(problems)} possible credential(s) in {scope}:\n")
        for name, line, label, detail in problems:
            print(f"  {name}:{line}")
            print(f"      {label}: {detail}")
        print("\nRead POSTGRES_PASSWORD and friends from the environment —")
        print("backend/db/dsn.py composes the DSN without a password literal.")
        print("If this is genuinely not a secret, add it to ALLOWED in")
        print("tests/check-secrets.py with the reason.\n")
        return 1

    print(f"No credentials in {len(files)} {scope}"
          f" ({len(secrets)} live values checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
