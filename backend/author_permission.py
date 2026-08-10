"""
Proof that someone controls an archive account, and what they permit.
=====================================================================

Neither AO3 nor FanFiction.net has an API or OAuth, and AO3 has publicly warned
its users never to hand their password to a third-party app. So "sign in with
AO3" is not available and should not be simulated: any form asking for those
credentials is the exact thing AO3 tells people to refuse.

What is available is proof of control. The author puts a one-time token
somewhere only they can edit — their own profile — and we read it back from the
public page. No credentials, one HTTP request per author, and the result is
publicly checkable by anyone, including the author.

Permitted by robots.txt, checked rather than assumed. AO3 disallows /works?,
/autocomplete/, /downloads/, /external_works/ and the search endpoints. /users/
is not disallowed, which is the path this uses.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not read consent out of prose. Fandom has a real convention for standing
permissions — the "blanket statement", usually in exactly the profile field this
reads — and it is tempting to parse. It should not be parsed:

  * they are overwhelmingly written about TRANSFORMATIVE works (podfic,
    translation, remix), not verbatim rehosting;
  * "archive" in them commonly means a fannish archive or a personal copy;
  * there is no standard format. Even the community's own permission-statement
    builder emits free prose.

Reading a blanket statement as permission to host would be the same error as
reading an ordinary summary as a refusal, which external_optout.py is carefully
built to avoid — just pointed the other way, where being wrong is worse. A
blanket statement is a reason to INVITE someone to verify. It is not the consent.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Long enough that it cannot be guessed, short enough to paste on a phone.
TOKEN_PREFIX = "ficatlas-verify-"
CHALLENGE_TTL = timedelta(hours=24)
MAX_ATTEMPTS = 12

SITES = ("ao3", "ffnet")

POLICIES = ("host", "metadata_only", "deny")

# A profile page is small, but a hung fetch here would hold a request thread.
FETCH_TIMEOUT = httpx.Timeout(connect=6.0, read=20.0, write=6.0, pool=6.0)

UA = ("FicAtlas/1.0 (+https://github.com/Georgexzy/ficatlas) "
      "author-verification; one request per author")


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(12)


def normalise_author(name: str) -> str:
    """Lookup key. Archives treat usernames case-insensitively for display but
    not always for URLs, so the display form is stored separately."""
    return (name or "").strip().lower()


def profile_url(site: str, author: str) -> str | None:
    a = (author or "").strip()
    if not a:
        return None
    if site == "ao3":
        # /users/<name>/profile is the page carrying the bio. Not disallowed by
        # AO3's robots.txt (unlike /works? and the search endpoints).
        return f"https://archiveofourown.org/users/{httpx.URL(path=a).path.lstrip('/')}/profile"
    if site == "ffnet":
        # FF.net profiles are numeric: /u/<id>/<name>. Accept either the id or a
        # full profile URL pasted in.
        m = re.search(r"/u/(\d+)", a)
        if m:
            return f"https://www.fanfiction.net/u/{m.group(1)}/"
        if a.isdigit():
            return f"https://www.fanfiction.net/u/{a}/"
        return None
    return None


class VerificationError(Exception):
    """Raised with a message written for the author, not for a log."""


# Cloudflare's own failures, which AO3 emits regularly and which say nothing
# about the profile being asked for. scheduler.py already treats these as
# retryable for crawls; the same is true here, and calling one "an error with
# that profile" would send an author off checking a username that is fine.
_TRANSIENT_STATUS = {502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527}
_RETRIES = 2


def fetch_profile(url: str) -> str:
    """Fetch a public profile page. Returns its text.

    Retries the transient failures rather than reporting them, because the first
    live test of this code hit an AO3 525 — Cloudflare failing to reach the
    origin — and told the author their profile could not be found. Being wrong in
    that direction sends someone to re-check a username that was correct.
    """
    last: str | None = None
    for attempt in range(_RETRIES + 1):
        try:
            with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT,
                              headers={"User-Agent": UA}) as client:
                r = client.get(url)
        except httpx.TimeoutException:
            last = ("The archive did not respond in time. That is usually "
                    "temporary — try again in a few minutes.")
        except httpx.HTTPError as e:
            log.warning("profile fetch failed for %s: %s: %s", url, type(e).__name__, e)
            last = "Could not reach the archive to check your profile."
        else:
            # Definitive answers, returned without retrying.
            if r.status_code == 404:
                raise VerificationError(
                    "No profile found at that address. Check the spelling of "
                    "your username — it has to match the archive exactly.")
            if r.status_code == 429:
                raise VerificationError(
                    "The archive is rate-limiting us at the moment. Wait a few "
                    "minutes and try again.")
            if r.status_code < 400:
                return r.text
            if r.status_code in _TRANSIENT_STATUS:
                last = ("The archive is having trouble at its end right now "
                        f"(error {r.status_code}) — this is not a problem with "
                        "your profile. Try again in a minute.")
            else:
                raise VerificationError(
                    f"The archive returned an error ({r.status_code}) for that "
                    "profile.")

        if attempt < _RETRIES:
            # AO3's 525s are frequently a single bad edge node; a short pause is
            # usually enough. Deliberately brief — someone is waiting on a form.
            import time
            time.sleep(1.5 * (attempt + 1))

    raise VerificationError(last or "Could not reach the archive.")


def token_present(page_text: str, token: str) -> bool:
    """Is the token actually on the page?

    Plain substring, deliberately. Anything cleverer — stripping punctuation,
    fuzzy matching, ignoring case — widens what counts as proof, and the whole
    value of this check is that it is narrow. The token is generated by us from
    `secrets`, so an exact appearance cannot happen by accident.
    """
    return bool(token) and token in (page_text or "")


def extract_evidence(page_text: str, token: str, window: int = 200) -> str:
    """The token with its surroundings, stored so the match can be re-examined
    later rather than taken on trust."""
    i = (page_text or "").find(token)
    if i < 0:
        return ""
    start = max(0, i - window)
    snippet = page_text[start:i + len(token) + window]
    # Collapse markup and whitespace; this is for a human reading an audit row.
    snippet = re.sub(r"<[^>]+>", " ", snippet)
    return re.sub(r"\s+", " ", snippet).strip()[:1000]


# ── Storage ──────────────────────────────────────────────────────────────────

def create_challenge(db: Session, site: str, author: str, source_ip: str | None) -> str:
    token = new_token()
    db.execute(sql_text("""
        INSERT INTO author_permission_challenges
            (token, site, author, expires_at, source_ip)
        VALUES (:t, :s, :a, :e, :ip)
    """), {"t": token, "s": site, "a": normalise_author(author),
           "e": datetime.now(timezone.utc) + CHALLENGE_TTL, "ip": source_ip})
    db.commit()
    return token


def load_challenge(db: Session, token: str) -> dict | None:
    row = db.execute(sql_text("""
        SELECT token, site, author, expires_at, attempts
        FROM author_permission_challenges WHERE token = :t
    """), {"t": token}).mappings().first()
    return dict(row) if row else None


def bump_attempts(db: Session, token: str) -> None:
    db.execute(sql_text(
        "UPDATE author_permission_challenges SET attempts = attempts + 1 "
        "WHERE token = :t"), {"t": token})
    db.commit()


def consume_challenge(db: Session, token: str) -> None:
    db.execute(sql_text(
        "DELETE FROM author_permission_challenges WHERE token = :t"), {"t": token})
    db.commit()


def purge_expired_challenges(db: Session) -> int:
    r = db.execute(sql_text(
        "DELETE FROM author_permission_challenges WHERE expires_at < now()"))
    db.commit()
    return r.rowcount or 0


def record_permission(db: Session, *, site: str, author: str, author_display: str,
                      policy: str, token: str, evidence_url: str,
                      evidence_text: str, contact_email: str | None) -> None:
    """Store a verified permission, replacing any previous one for this author.

    ON CONFLICT rather than insert-only: an author changing their mind is the
    normal case, not an error, and their latest verified statement is the one
    that counts. revoked_at is cleared because re-verifying IS the un-revoking.
    """
    db.execute(sql_text("""
        INSERT INTO author_permissions
            (site, author, author_display, policy, token, evidence_url,
             evidence_text, contact_email, verified_at, created_at, updated_at)
        VALUES (:s, :a, :ad, :p, :t, :eu, :et, :em, now(), now(), now())
        ON CONFLICT (site, author) DO UPDATE SET
            policy        = EXCLUDED.policy,
            author_display= EXCLUDED.author_display,
            token         = EXCLUDED.token,
            evidence_url  = EXCLUDED.evidence_url,
            evidence_text = EXCLUDED.evidence_text,
            contact_email = COALESCE(EXCLUDED.contact_email, author_permissions.contact_email),
            verified_at   = now(),
            updated_at    = now(),
            revoked_at    = NULL
    """), {"s": site, "a": normalise_author(author), "ad": author_display,
           "p": policy, "t": token, "eu": evidence_url, "et": evidence_text,
           "em": contact_email})
    db.commit()


def get_permission(db: Session, site: str, author: str) -> dict | None:
    """The active permission for an author, or None.

    None means "never asked", which is NOT the same as "said no" — the caller
    decides the default, and the default is the existing behaviour rather than
    anything this table implies.
    """
    row = db.execute(sql_text("""
        SELECT site, author, author_display, policy, verified_at, contact_email
        FROM author_permissions
        WHERE site = :s AND author = :a AND revoked_at IS NULL
    """), {"s": site, "a": normalise_author(author)}).mappings().first()
    return dict(row) if row else None


def decide_hosting(db: Session, *, site: str, author: str, summary: str | None,
                   private: bool) -> tuple[bool, str | None]:
    """One verdict on whether a work may go into the public index.

    Two sources of signal existed separately and would have drifted apart if
    left that way: a heuristic read of the work's summary (external_optout.py)
    and, now, a verified statement from the author. This is where they meet.

    Order of authority, strongest first:

      1. A PRIVATE import. Nothing is republished — the reader keeps a copy only
         they can read — so no third party's consent is engaged and neither
         signal applies. This is long-standing behaviour (see api/library.py)
         and the reason an owner keeps full access to their own library
         regardless of what any author has said about public hosting.

      2. A VERIFIED permission. Someone who proved they control the account and
         then said "host" outranks a guess made by a regular expression about
         their own prose — including a guess that says the opposite. This is the
         only route by which a false positive in the opt-out detector can be
         corrected, and it can only be exercised by the person entitled to
         correct it.

      3. The opt-out heuristic, unchanged, for the overwhelming majority of
         authors who have never heard of this site.

    Returns (allowed, reason). `reason` is None when allowed and is written for
    the person who will read it.
    """
    if private:
        return True, None

    perm = get_permission(db, site, author) if author else None
    if perm:
        if perm["policy"] == "host":
            return True, None
        if perm["policy"] == "metadata_only":
            return False, ("This work's author has asked that FicAtlas list "
                           "their work but not store its text.")
        if perm["policy"] == "deny":
            return False, ("This work's author has asked FicAtlas not to index "
                           "their work.")

    from external_optout import has_external_optout
    if has_external_optout(summary):
        return False, ("This work's author states it must not be reposted on "
                       "other sites, so FicAtlas won't add it to the shared index.")
    return True, None


def revoke_permission(db: Session, site: str, author: str) -> bool:
    r = db.execute(sql_text("""
        UPDATE author_permissions SET revoked_at = now(), updated_at = now()
        WHERE site = :s AND author = :a AND revoked_at IS NULL
    """), {"s": site, "a": normalise_author(author)})
    db.commit()
    return (r.rowcount or 0) > 0
