"""
Sanitise scraped chapter HTML before it reaches a reader's browser.
==================================================================

Chapter bodies are scraped from AO3, FF.net, FicAlley and DarkLordPotter, or
uploaded directly as EPUB, and the reader injects them with
`dangerouslySetInnerHTML`. Nothing sanitised them anywhere in between, so any
markup in a fic ran in the reader's page — and the app has logged-in accounts,
so that is a session-stealing hole and not a theoretical one. The EPUB upload
path makes it directly reachable: the attacker supplies the file.

Two jobs, both of which turned out to matter:

  * SECURITY — drop scripts, event handlers and javascript: URLs.
  * READABILITY — scrapers captured page furniture along with the story. A real
    stored FicAlley chapter contains a table of site navigation, an "Add to
    Address Book" link and the text "This is spam", all rendered as if it were
    prose. Unwrapping layout tags removes the scaffolding and keeps the words.

An explicit allowlist rather than a blocklist: anything not named here cannot
get through, so a tag nobody thought of fails closed. lxml is already a
dependency; bleach and nh3 are not, and lxml's own Cleaner moved out of the
package in lxml 5.
"""

import re

from lxml import etree, html

# Dropped along with everything inside them — none of it is ever story text.
DROP_ENTIRELY = {
    "script", "style", "iframe", "object", "embed", "applet", "form", "input",
    "button", "select", "textarea", "noscript", "svg", "math", "link", "meta",
    "base", "frame", "frameset", "audio", "video", "canvas", "map", "area",
}

# Kept as-is. Everything a fic legitimately uses to format prose.
ALLOWED_TAGS = {
    "p", "br", "hr", "div", "span",
    "em", "i", "b", "strong", "u", "s", "strike", "del", "ins", "mark", "small",
    "sub", "sup", "blockquote", "q", "cite", "abbr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "pre", "code", "kbd", "samp", "var",
    "a", "img", "figure", "figcaption", "center", "ruby", "rt", "rp",
}

# Per-tag attribute allowlist. Everything else goes, which is what removes the
# on* handlers, inline styles and the scrapers' class/id soup in one pass.
ALLOWED_ATTRS = {
    # rel/target are set by us on outbound links, never taken from the source.
    "a":   {"href", "title", "rel", "target"},
    "img": {"src", "alt", "title"},
    "*":   {"lang", "dir"},
}

# Anything that is not plainly a document reference. javascript: is the obvious
# one; data: can carry an HTML payload, and vbscript: still exists in the wild.
_BAD_URL = re.compile(r"^\s*(javascript|vbscript|data|file|about)\s*:", re.I)


def _url_ok(value: str) -> bool:
    return bool(value) and not _BAD_URL.match(value)


def sanitize_html(raw: str | None) -> str:
    """Return `raw` with only allowlisted markup left. Never raises."""
    if not raw or not raw.strip():
        return ""
    try:
        # A chapter is a fragment, not a document. wrap it so lxml does not
        # invent <html><body> around it and so multiple top-level nodes survive.
        root = html.fragment_fromstring(raw, create_parent="div")
    except Exception:
        # Unparseable: fall back to text only rather than passing markup through.
        return re.sub(r"<[^>]+>", "", raw).strip()

    for el in list(root.iter()):
        if el is root:
            continue
        tag = el.tag
        if not isinstance(tag, str):
            # Comments and processing instructions carry no prose and can hide
            # conditional-comment markup, so they go.
            el.getparent().remove(el)
            continue
        tag = tag.lower()

        if tag in DROP_ENTIRELY:
            parent = el.getparent()
            if parent is not None:
                # Keep the tail text — it is the prose that followed the tag.
                if el.tail:
                    prev = el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + el.tail
                    else:
                        parent.text = (parent.text or "") + el.tail
                parent.remove(el)
            continue

        if tag not in ALLOWED_TAGS:
            # Unwrap: this is what strips <table>/<td> page scaffolding while
            # keeping any real words that were laid out inside it.
            el.drop_tag()
            continue

        allowed = ALLOWED_ATTRS.get(tag, set()) | ALLOWED_ATTRS["*"]
        for name, value in list(el.attrib.items()):
            if name.lower() not in allowed:
                del el.attrib[name]
            elif name.lower() in ("href", "src") and not _url_ok(value):
                del el.attrib[name]

        # Anything leaving the site opens in a new tab and must not hand the
        # opener over to the destination page.
        if tag == "a" and el.get("href", "").startswith(("http://", "https://")):
            el.set("rel", "noopener noreferrer nofollow")
            el.set("target", "_blank")

    out = (root.text or "")
    for child in root:
        out += etree.tostring(child, encoding="unicode", method="html")
    return out.strip()
