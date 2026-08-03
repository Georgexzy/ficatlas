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
    # EPUB navigation documents get imported as chapters by some sources — a
    # <nav epub:type="toc"> full of links to the other chapters. It is the
    # book's furniture, not a chapter, and rendering it hands the reader a list
    # of links where the prose should be.
    "nav",
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


# ── Redundant chapter headings ───────────────────────────────────────────────
#
# The reader prints the chapter three times: "Chapter 1 of 12" in the
# breadcrumb, the stored chapter title as an <h1>, and then the body opens with
# its own "Chapter 1 - The Competition" because that is how the author wrote the
# file and the importer kept it verbatim.
#
# Measured: 26,030 of 82,161 stored chapters (32%) begin with such a heading,
# and 46,254 (56%) have a title as uninformative as "Chapter 01".
#
# So the body's heading is usually the BETTER one — it carries the actual name.
# Rather than discard it, promote it to the title when the stored title is
# generic, and drop it from the body either way. Done at serve time, so it
# applies to everything already imported with no migration.

_CH_WORD = r"(?:chapter|chap|ch|part|pt|prologue|epilogue|interlude)"
_ORDINAL = (r"(?:\d{1,3}|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
            r"nineteen|twenty)")

# "Chapter 1", "Ch. 03 — The Trip", "Part Two: Arrival", "Prologue"
_HEADING_RE = re.compile(
    rf"^\s*{_CH_WORD}\b\.?\s*(?:{_ORDINAL})?\s*(?:[-–—:.|]+\s*(?P<title>.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)

# A heading is short. Anything longer is prose that merely opens with the word,
# e.g. "Chapter one of his life had closed, and he was not sorry to see it go."
_MAX_HEADING_CHARS = 90

# How many leading blocks may be furniture. Enough for
# title / rule / chapter-title / rule, not enough to eat a short opening scene.
_MAX_LEADING_BLOCKS = 5

_GENERIC_TITLE_RE = re.compile(
    rf"^\s*(?:{_CH_WORD}\b\.?\s*(?:{_ORDINAL})?|\d{{1,3}})\s*$", re.IGNORECASE)


def is_generic_title(title: str | None) -> bool:
    """True for titles that say nothing the breadcrumb does not already."""
    return not (title or "").strip() or bool(_GENERIC_TITLE_RE.match(title or ""))


# A lone "*" or "~" used as a decorative rule. Distinct from a scene break,
# which needs two or more marks — a single one is nearly always part of an EPUB
# header block rather than a break between scenes.
_LONE_MARK_RE = re.compile(r"^[\s ]*[*~\-=_·•—–+#][\s ]*$")


def _norm_title(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (v or "").lower())


def strip_chapter_heading(html_text: str, stored_title: str | None,
                          story_title: str | None = None) -> tuple[str, str | None]:
    """Drop a leading chapter heading from the body.

    Returns (content, better_title). `better_title` is set only when the heading
    carried a real name and the stored title did not.
    """
    if not html_text or not html_text.strip():
        return html_text or "", None
    try:
        root = html.fragment_fromstring(html_text, create_parent="div")
    except Exception:
        return html_text, None

    # EPUBs routinely open a chapter with a header BLOCK, not a single line:
    #
    #     Darkness Falling      <- the STORY title, repeated
    #     *                     <- decorative rule
    #     Feel the Love         <- the actual chapter title
    #     *
    #     "Severus," the blond man replied quietly...
    #
    # Four lines of furniture above the prose, while the heading above it all
    # says "Chapter 02". So walk the leading blocks rather than testing one:
    # drop the story title, drop lone marks, drop "Chapter N" headings, and keep
    # the best candidate for a real chapter title along the way.
    story_key = _norm_title(story_title or "")
    better: str | None = None
    removed: list = []

    for el in list(root)[:_MAX_LEADING_BLOCKS]:
        text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
        if not text:
            removed.append(el)          # empty spacer above the header
            continue
        if len(text) > _MAX_HEADING_CHARS:
            break                       # prose — stop
        if _LONE_MARK_RE.match(text):
            removed.append(el)
            continue
        if story_key and _norm_title(text) == story_key:
            removed.append(el)          # the story's own title, repeated
            continue
        m = _HEADING_RE.match(text)
        if m:
            better = better or ((m.group("title") or "").strip() or None)
            removed.append(el)
            continue
        # A short line that is not prose, not a mark and not the story title is
        # the chapter's real name. Require no sentence-ending punctuation and no
        # opening quote so dialogue and one-line paragraphs are left alone.
        if (not better and len(text) <= 70
                and re.search(r"[A-Za-z0-9]", text)      # never a row of marks
                and not text[0] in "\"'“‘«-–—"
                and not text.rstrip().endswith((".", "!", "?", ",", ";", ":"))):
            better = text
            removed.append(el)
            continue
        break

    if not removed:
        return html_text, None
    for el in removed:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)

    out = (root.text or "")
    for child in root:
        out += etree.tostring(child, encoding="unicode", method="html")
    return out.strip(), (better if better and is_generic_title(stored_title) else None)


# ── Formatting tidy ──────────────────────────────────────────────────────────
#
# Measured across the 82,161 stored chapters:
#
#     40,919 (50%)  contain empty <p> or <p>&nbsp;</p> blocks
#     29,529 (36%)  use a typed line like "* * *" or "---" as a scene break
#
# The empty paragraphs are the visible problem: EPUB and scraped HTML use them
# as spacing, and the reader already sets its own paragraph margins, so each one
# becomes a blank line and a long fic turns into a column of gaps.
#
# Deliberately NOT removed: disclaimers, author's notes and "please review"
# endings. Those appear in 1,089, 2,169 and 858 chapters respectively and are
# the author's own words — cleanup should tidy the markup a fic arrived in, not
# edit what it says.

# Two or more break characters, adjacent OR spaced: "***", "* * *", "---",
# "~ ~ ~". Requiring them adjacent missed the spaced form, which is the one
# most authors actually type.
_SCENE_BREAK_RE = re.compile(r"^[\s ]*(?:[*~\-=_·•—–+#][\s ]*){2,}$")


def _is_blank(el) -> bool:
    """An element carrying nothing a reader would see."""
    if el.tag in ("img", "hr", "br"):
        return False
    if el.find(".//img") is not None or el.find(".//hr") is not None:
        return False
    text = "".join(el.itertext()).replace(" ", " ")
    return not text.strip()


def tidy_chapter_html(html_text: str) -> str:
    """Remove spacer markup and normalise scene breaks. Never raises."""
    if not html_text or not html_text.strip():
        return html_text or ""
    try:
        root = html.fragment_fromstring(html_text, create_parent="div")
    except Exception:
        return html_text

    # Flatten layout wrappers FIRST.
    #
    # Scraped and EPUB HTML nests prose inside anonymous <div>s used purely for
    # layout — one real fic wraps its chapter as
    # <div><div><div>Chapter 1</div><div><p>…prose…</p></div></div></div>, which
    # hides the heading lines from anything that walks top-level children. The
    # reader styles paragraphs, not containers, so the wrappers carry no meaning
    # worth keeping and flattening them puts every block at one level where the
    # heading and spacer passes below can actually see it.
    for _ in range(6):                      # bounded: deep nesting is finite
        wrappers = [el for el in root.iter()
                    if isinstance(el.tag, str) and el is not root
                    and el.tag in ("div", "section", "article")
                    and len(el) > 0]
        if not wrappers:
            break
        for el in wrappers:
            try:
                el.drop_tag()               # keeps children, text and tail
            except Exception:
                pass

    for el in list(root.iter()):
        if el is root or not isinstance(el.tag, str):
            continue

        # A typed scene break becomes a real <hr>, which the reader already
        # styles as "* * *" — so every fic separates scenes the same way
        # regardless of which punctuation its author reached for.
        # <center> is what EPUB and old FFN HTML actually use for a centred
        # rule; leaving it out meant "<center>* * * * *</center>" survived tidy
        # and was then promoted into the chapter TITLE.
        if el.tag in ("p", "div", "center", "blockquote",
                      "h1", "h2", "h3", "h4", "h5", "h6"):
            text = "".join(el.itertext()).strip()
            if text and _SCENE_BREAK_RE.match(text):
                el.clear()
                el.tag = "hr"
                el.tail = None
                continue

        # Spacer paragraphs. The reader sets its own paragraph spacing, so these
        # only ever add blank lines.
        if el.tag in ("p", "div", "span") and _is_blank(el):
            parent = el.getparent()
            if parent is not None:
                if el.tail and el.tail.strip():
                    prev = el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + el.tail
                    else:
                        parent.text = (parent.text or "") + el.tail
                parent.remove(el)

    # Runs of <br> are the other way spacing gets faked; two is a paragraph
    # break, more than that is padding.
    out = (root.text or "")
    for child in root:
        out += etree.tostring(child, encoding="unicode", method="html")
    out = re.sub(r"(?:\s*<br\s*/?>\s*){3,}", "<br><br>", out, flags=re.I)
    # Consecutive rules after the above collapse into one.
    out = re.sub(r"(?:\s*<hr\s*/?>\s*){2,}", "<hr>", out, flags=re.I)
    return out.strip()
