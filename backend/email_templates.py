"""Every email FicAtlas sends, as HTML and plain text from one layout.

Built to the conventions transactional mail has settled on, because mail
clients are not browsers and each fault below is one somebody has shipped:

* One 600px column of TABLES with inline styles. Outlook for Windows renders
  with Word's engine (no flexbox, no max-width on divs, no border-radius on
  buttons), and Gmail strips <style> in some contexts, so anything that must
  survive is inline. The <style> block only ADDS: dark mode and phone padding.
* A hidden preheader: the line inbox lists show after the subject. Left
  empty, clients fill it with the first text they find ("View in browser").
* One call to action per email, as a "bulletproof" button: a real link with
  padding for every client, and a VML roundrect inside an Outlook conditional
  comment so it is still a button there. 44px+ tall, 16px text.
* The logo is a PNG on a transparent background (SVG is unsupported in Gmail
  and Outlook), two golds that read on white and on dark. The wordmark is
  TEXT, so it follows a client's dark mode instead of becoming a white box,
  and an email with images off still says who it is from.
* `color-scheme: light dark` and a prefers-color-scheme block for the clients
  that honour them (Apple Mail, Outlook.com, some Gmail apps); colours are
  chosen to survive the clients that force-invert instead.
* role="presentation" on layout tables, lang and dir on the document, real
  headings, alt text: screen readers read mail too.
* A plain-text twin of every message, written as text rather than stripped
  HTML, sent as multipart/alternative (see mailer.send).

The copy follows the same rules for every email: say who it is for, say what
happened in one line, one thing to do, what to do if you did not expect it,
and how to reach a person.
"""
from __future__ import annotations

import html
import os
from datetime import datetime

SITE = (os.getenv("SITE_URL", "") or os.getenv("PUBLIC_BASE_URL", "")
        or "https://ficatlas.com").rstrip("/")
HELP = "help@ficatlas.com"
MARK = f"{SITE}/email-mark.png"

# The site's own palette (frontend/app/globals.css, light theme), darkened
# where small text needs 4.5:1 on white.
INK = "#1f1d1a"
INK_DIM = "#4d4740"
INK_FAINT = "#6f675c"
GOLD = "#8a6a28"
PAPER = "#f4efe4"
CARD = "#ffffff"
RULE = "#e6dfd0"

SITE_LABELS = {"ao3": "AO3", "ffnet": "FanFiction.net", "fictionalley": "FictionAlley"}

_SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, "
         "sans-serif")
_SERIF = "Georgia, 'Times New Roman', serif"


def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def button(href: str, label: str) -> str:
    """A link that is a button everywhere, including Outlook for Windows."""
    h, l = e(href), e(label)
    return f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:8px 0 4px">
  <tr><td>
    <!--[if mso]>
    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word"
      href="{h}" style="height:48px;v-text-anchor:middle;width:280px" arcsize="17%"
      strokecolor="{GOLD}" fillcolor="{GOLD}">
      <w:anchorlock/><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:16px;font-weight:bold">{l}</center>
    </v:roundrect>
    <![endif]-->
    <!--[if !mso]><!-->
    <a href="{h}" class="btn"
       style="display:inline-block;background:{GOLD};color:#ffffff;font-family:{_SANS};
              font-size:16px;font-weight:600;line-height:20px;text-decoration:none;
              padding:14px 26px;border-radius:8px;mso-hide:all">{l}</a>
    <!--<![endif]-->
  </td></tr>
</table>"""


def layout(*, title: str, preheader: str, body: str, footer: str) -> str:
    """The shell every email shares."""
    year = datetime.now().year
    return f"""<!doctype html>
<html lang="en" dir="ltr" xmlns:v="urn:schemas-microsoft-com:vml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{e(title)}</title>
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
<style>
  :root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
  a {{ color: {GOLD}; }}
  @media (max-width: 620px) {{
    .shell {{ width: 100% !important; }}
    .pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
    .btn {{ display: block !important; text-align: center !important; }}
  }}
  @media (prefers-color-scheme: dark) {{
    .bg {{ background: #111010 !important; }}
    .card {{ background: #1b1a18 !important; border-color: #2d2a26 !important; }}
    .ink {{ color: #ede9e0 !important; }}
    .dim {{ color: #c9c2b5 !important; }}
    .faint {{ color: #a39b8e !important; }}
    .rule {{ border-color: #2d2a26 !important; }}
    .tile {{ background: #232120 !important; border-color: #2d2a26 !important; }}
    .code {{ background: #232120 !important; color: #ede9e0 !important; }}
    a, .gold {{ color: #d9b977 !important; }}
    a.btn {{ color: #ffffff !important; background: #8a6a28 !important; }}
    .note {{ background: #2a2416 !important; border-color: #4a3d1f !important; color: #ede9e0 !important; }}
  }}
</style>
</head>
<body class="bg" style="margin:0;padding:0;background:{PAPER};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{e(preheader)}&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>
<table role="presentation" class="bg" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:{PAPER}">
<tr><td align="center" style="padding:28px 12px">
  <table role="presentation" class="shell" width="600" cellspacing="0" cellpadding="0" border="0" style="width:600px;max-width:600px">
    <tr><td class="pad" style="padding:0 32px 18px">
      <a href="{SITE}" style="text-decoration:none">
        <img src="{MARK}" width="28" height="28" alt="" style="vertical-align:middle;border:0;outline:none">
        <span class="ink" style="font-family:{_SERIF};font-size:22px;font-weight:bold;color:{INK};vertical-align:middle;letter-spacing:-0.3px">Fic</span><span class="gold" style="font-family:{_SERIF};font-size:22px;font-weight:bold;font-style:italic;color:{GOLD};vertical-align:middle">Atlas</span>
      </a>
    </td></tr>
    <tr><td class="card pad" style="background:{CARD};border:1px solid {RULE};border-radius:12px;padding:32px">
      {body}
    </td></tr>
    <tr><td class="pad faint" style="padding:22px 32px 0;font-family:{_SANS};font-size:12.5px;line-height:19px;color:{INK_FAINT}">
      {footer}
      <p style="margin:12px 0 0">FicAtlas · search AO3, FanFiction.net and FictionAlley at once ·
        <a href="{SITE}" style="color:{INK_FAINT}">ficatlas.com</a> ·
        <a href="mailto:{HELP}" style="color:{INK_FAINT}">{HELP}</a></p>
      <p style="margin:6px 0 0">© {year} FicAtlas. Non-commercial, no ads, no tracking pixels in this email.</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


def h1(text: str) -> str:
    return (f'<h1 class="ink" style="margin:0 0 14px;font-family:{_SERIF};font-size:26px;'
            f'line-height:32px;font-weight:bold;color:{INK}">{e(text)}</h1>')


def p(inner_html: str, *, dim: bool = False, size: int = 16, margin: str = "0 0 14px") -> str:
    color, cls = (INK_DIM, "dim") if dim else (INK, "ink")
    return (f'<p class="{cls}" style="margin:{margin};font-family:{_SANS};font-size:{size}px;'
            f'line-height:{int(size * 1.55)}px;color:{color}">{inner_html}</p>')


def rule() -> str:
    return (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
            f'<tr><td class="rule" style="border-top:1px solid {RULE};font-size:0;line-height:0;'
            f'padding:0;margin:0;height:1px">&nbsp;</td></tr></table>')


# ── Password reset ──────────────────────────────────────────────────────────

def reset_email(*, username: str, link: str | None, code: str, ttl_min: int):
    """(subject, text, html) for a password reset.

    Names the account (one address can hold several), one button, the code as
    a fallback, the expiry plainly, and what to do if you did not ask. Not
    "someone asked to reset…": it is almost always the reader, seconds after
    pressing the button, and "someone" reads as a stranger.
    """
    subject = "Reset your FicAtlas password"
    pre = f"Choose a new password for {username}. The link works once, for {ttl_min} minutes."
    bare = SITE.replace("https://", "")
    text = "\n".join(filter(None, [
        f"Hi {username},", "",
        "Use the link below to choose a new password for your FicAtlas account.", "",
        f"    {link}" if link else None,
        "" if link else None,
        f"The link works once and expires in {ttl_min} minutes.", "",
        f"If the link does not open, go to {bare}/reset and enter this code:", "",
        f"    {code}", "",
        "Did not ask for this? You can ignore this email. Your password stays",
        "the same unless the link above is used, and only this mailbox has it.", "",
        f"Need help? Write to {HELP}.", "",
        "-- ", "FicAtlas", "Search AO3, FanFiction.net and FictionAlley at once", SITE,
    ]))
    body = (
        h1("Reset your password")
        + p(f"Hi {e(username)}, use the button below to choose a new password for "
            f"your FicAtlas account <strong>{e(username)}</strong>.")
        + (button(link, "Choose a new password") if link else "")
        + p(f"The link works once and expires in <strong>{ttl_min} minutes</strong>.",
            dim=True, size=14, margin="12px 0 22px")
        + rule()
        + p(f"Button not working? Go to <a href=\"{e(SITE)}/reset\">{e(bare)}/reset</a> "
            f"and enter this code:", dim=True, size=14, margin="20px 0 8px")
        + (f'<p class="code" style="margin:0 0 22px;padding:12px 14px;background:{PAPER};'
           f'border-radius:8px;font-family:Menlo,Consolas,monospace;font-size:15px;'
           f'letter-spacing:0.5px;color:{INK};word-break:break-all">{e(code)}</p>')
        + p("<strong>Didn't ask for this?</strong> You can ignore this email. Your "
            "password stays the same unless the link above is used, and only this "
            "mailbox has it.", dim=True, size=14, margin="0")
    )
    footer = (f"You are receiving this because a password reset was requested for the "
              f"FicAtlas account {e(username)}.")
    return subject, text, layout(title=subject, preheader=pre, body=body, footer=footer)


# ── Followed stories updated ────────────────────────────────────────────────

FREQ_LABEL = {"daily": "as a daily digest", "hourly": "as updates happen"}


def _story_tile(sid, title, author, site, chapters, new) -> str:
    url = f"{SITE}/story/{sid}"
    meta = " · ".join(x for x in [f"by {e(author)}" if author else "",
                                   e(SITE_LABELS.get(site, site or ""))] if x)
    badge = f"+{new} new chapter{'s' if new != 1 else ''}"
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 10px">
  <tr><td class="tile" style="background:{PAPER};border:1px solid {RULE};border-radius:10px;padding:14px 16px">
    <a href="{e(url)}" class="ink" style="font-family:{_SERIF};font-size:18px;line-height:24px;font-weight:bold;color:{INK};text-decoration:none">{e(title)}</a>
    <p class="faint" style="margin:4px 0 8px;font-family:{_SANS};font-size:13px;line-height:19px;color:{INK_FAINT}">{meta}</p>
    <p style="margin:0;font-family:{_SANS};font-size:14px;line-height:20px">
      <span class="gold" style="color:{GOLD};font-weight:600">{e(badge)}</span>
      <span class="faint" style="color:{INK_FAINT}"> · now {chapters} chapters · </span>
      <a href="{e(url)}" style="color:{GOLD};font-weight:600;text-decoration:none">Read →</a>
    </p>
  </td></tr>
</table>"""


def digest_email(*, username: str, works: list, token: str, frequency: str = "daily",
                 max_listed: int = 25, test: bool = False):
    """(subject, text, html) for the followed-stories update.

    `works` rows are (story_id, title, author, site, chapter_count, new).
    """
    n = len(works)
    first = works[0]
    if n == 1:
        subject = f"New chapter{'s' if first[5] != 1 else ''}: {first[1]}"
        lead = (f"<strong>{e(first[1])}</strong> has {first[5]} new chapter"
                f"{'s' if first[5] != 1 else ''}.")
    else:
        subject = f"{n} stories you follow have new chapters"
        lead = f"{n} stories you follow have new chapters."
    if test:
        subject = "[Test] " + subject
    pre = ", ".join(f"{w[1]} (+{w[5]})" for w in works[:3]) + ("…" if n > 3 else "")
    unsub_page = f"{SITE}/unsubscribe#t={token}"
    following = f"{SITE}/library?tab=following"

    lines = [f"Hi {username},", ""]
    if test:
        lines += ["This is a TEST of your FicAtlas update emails. It shows what a real",
                  "one looks like, using stories you follow; they may not have updated.", ""]
    lines += [lead.replace("<strong>", "").replace("</strong>", ""), ""]
    for sid, title, author, site, chapters, new in works[:max_listed]:
        lines += [f"  {title}" + (f" by {author}" if author else ""),
                  f"  +{new} new chapter{'s' if new != 1 else ''} (now {chapters}) · "
                  f"{SITE_LABELS.get(site, site)}",
                  f"  {SITE}/story/{sid}", ""]
    if n > max_listed:
        lines += [f"…and {n - max_listed} more on your Following list.", ""]
    lines += ["Everything you follow, across every archive, in one list:",
              f"  {following}", "",
              f"You get these emails {FREQ_LABEL.get(frequency, '')} because you",
              "turned on update emails.",
              "To stop them:", f"  {unsub_page}", "",
              "-- ", "FicAtlas", "Search AO3, FanFiction.net and FictionAlley at once", SITE]
    text = "\n".join(lines)

    test_note = ""
    if test:
        test_note = (f'<p class="note" style="margin:0 0 18px;padding:10px 14px;border-radius:8px;'
                     f'background:#fff8e6;border:1px solid #ecd9a8;font-family:{_SANS};'
                     f'font-size:14px;line-height:20px;color:{INK_DIM}"><strong>This is a test.</strong> '
                     f'It shows what an update email looks like, using stories you follow — '
                     f'they may not actually have updated.</p>')
    tiles = "".join(_story_tile(*w) for w in works[:max_listed])
    more = (p(f"…and {n - max_listed} more on your Following list.", dim=True, size=14)
            if n > max_listed else "")
    body = (
        test_note
        + h1("New chapters" if not test else "Your update emails are on")
        + p(f"Hi {e(username)}, {lead}")
        + tiles + more
        + '<div style="height:8px;line-height:8px;font-size:0">&nbsp;</div>'
        + button(following, "Open your Following list")
        + p("One list for everything you follow on AO3, FanFiction.net and FictionAlley.",
            dim=True, size=13, margin="10px 0 0")
    )
    footer = (f"You get these emails {FREQ_LABEL.get(frequency, '')} because you turned on "
              f"update emails at FicAtlas. "
              f'<a href="{e(following)}" style="color:{INK_FAINT}">Change how often</a> · '
              f'<a href="{e(unsub_page)}" style="color:{INK_FAINT}">Unsubscribe</a>')
    headers = {
        # RFC 8058 one-click: mail clients show their own Unsubscribe button and
        # POST here without opening a page. Gmail and Yahoo require it of
        # bulk senders, and it is what keeps a digest out of spam.
        "List-Unsubscribe": f"<{SITE}/api/follows/email/unsubscribe?t={token}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return subject, text, layout(title=subject, preheader=pre, body=body, footer=footer), headers


def setup_test_email(*, username: str):
    """(subject, text, html) when a reader tests update emails with no follows."""
    subject = "[Test] Your FicAtlas update emails work"
    pre = "Follow a story and you will hear when it gains chapters."
    text = "\n".join([
        f"Hi {username},", "",
        "This is a test: update emails reach this address.",
        "Follow any story and you will get an email when it gains chapters.", "",
        f"Find something to follow: {SITE}", "",
        "-- ", "FicAtlas", SITE])
    body = (h1("Update emails work")
            + p(f"Hi {e(username)}, this is a test: update emails reach this address.")
            + p("Follow any story — from AO3, FanFiction.net or FictionAlley — and you "
                "will get an email when it gains chapters.", dim=True, size=15)
            + button(SITE, "Find something to follow"))
    footer = "You are receiving this because you asked for a test email from your Following list."
    return subject, text, layout(title=subject, preheader=pre, body=body, footer=footer)
