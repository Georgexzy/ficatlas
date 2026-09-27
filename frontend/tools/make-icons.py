#!/usr/bin/env python3
"""Generate FicAtlas's app icons from the site's own palette and typeface.

Why this is a script and not a folder of binaries
-------------------------------------------------
The icons it replaces were periwinkle blue (#7f8cf0-ish) on near-black, in a
generic serif — from a palette this site has not used for a long time. Nothing
tied them to the wordmark in the header, so the tab icon, the phone home-screen
icon and the site disagreed about what FicAtlas looks like, and there was no way
to re-cut them when the palette moved again. Now there is: change a token here,
re-run, and every size follows.

The design is the WORDMARK, not a new mark
------------------------------------------
The header renders `Fic<em>Atlas</em>` — Playfair Display, "Fic" in the text
colour and "Atlas" in accent gold italic. The icon is that lockup reduced to its
two capitals: F upright in cream, A italic in gold. Someone who has seen the
header recognises the icon, which is the whole job of an app icon and is what
the blue "FA" could not do.

Deliberately NOT a picture of a book, a scroll or a globe. Those shapes are
already spoken for in app/SiteIcon.tsx, where they mean AO3, FanFiction.net and
FictionAlley respectively — an icon that reused one would be claiming the site is
an archive rather than an index of them.

Colours come from app/globals.css and must stay in step with it:
    --bg      #111010   the dark ground the site actually uses
    --text    #ede9e0   cream
    --accent  #c8a96e   warm gold
    --border  the hairline frame, at low opacity

One ground for both themes, on purpose: a tab icon and a home-screen icon sit on
furniture we do not control (a browser chrome, a phone wallpaper), so a
transparent or theme-reactive icon disappears against half of them. The dark tile
is self-contained and legible on anything.

Usage:
    python3 frontend/tools/make-icons.py [--fonts DIR] [--out DIR]

Fonts are Playfair Display (OFL), fetched once from the google/fonts repo and
cached in --fonts. They are NOT committed: they are 300kB each, the site loads
the same family from Google Fonts at runtime, and a font binary in the tree is a
licence file somebody has to remember to carry with it.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── the site's palette, from app/globals.css ────────────────────────────────
BG = (17, 16, 16)        # --bg          #111010
CREAM = (237, 233, 224)  # --text        #ede9e0
GOLD = (200, 169, 110)   # --accent      #c8a96e

FONTS = {
    "roman": ("PlayfairDisplay[wght].ttf",
              "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/"
              "PlayfairDisplay%5Bwght%5D.ttf"),
    "italic": ("PlayfairDisplay-Italic[wght].ttf",
               "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/"
               "PlayfairDisplay-Italic%5Bwght%5D.ttf"),
    # The site's body face, for the card's subtitle — the same pairing the pages
    # use, so a shared link looks like the site it opens.
    "sans": ("DMSans[opsz,wght].ttf",
             "https://github.com/google/fonts/raw/main/ofl/dmsans/"
             "DMSans%5Bopsz%2Cwght%5D.ttf"),
}

# What each size is for. Every one of these is referenced by something — the
# manifest, the <head>, or Google's OAuth consent screen — so a size added here
# without a consumer is dead weight, and a consumer without a size is a 404.
SIZES = {
    "icon-48.png": 48,      # favicon fallback
    "icon-192.png": 192,    # PWA manifest
    "icon-512.png": 512,    # PWA manifest, splash
    "apple-icon.png": 180,  # iOS home screen
    # Google's OAuth consent screen. It asks for a square image and displays it
    # at small sizes beside the app name on the "Sign in with FicAtlas" dialog;
    # 120px is the size Google documents.
    "logo-120.png": 120,
}

# Rendered at 8x and downsampled. Playfair is a high-contrast didone — its thin
# strokes are where the character is, and rasterising them straight at 48px
# breaks them up. Supersampling keeps the hairlines as grey rather than as gaps.
SS = 8


def fetch_fonts(dirpath: Path) -> dict[str, Path]:
    dirpath.mkdir(parents=True, exist_ok=True)
    out = {}
    for style, (name, url) in FONTS.items():
        p = dirpath / name
        if not p.exists():
            print(f"  fetching {name} …", file=sys.stderr)
            req = urllib.request.Request(url, headers={"User-Agent": "ficatlas-icons"})
            with urllib.request.urlopen(req, timeout=60) as r:
                p.write_bytes(r.read())
        out[style] = p
    return out


def _weighted(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """Playfair ships as a variable font. The wordmark is 600, so the icon is."""
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass          # a static build, or a Pillow without variation support
    return f


def draw_icon(px: int, fonts: dict[str, Path]) -> Image.Image:
    S = px * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    small = px <= 64          # see the note on the type size below

    # The tile. Radius is 22% of the side — the same soft-square the rest of the
    # site uses for cards, and short of the 50% that would read as a circle.
    pad = round(S * 0.055)
    d.rounded_rectangle([pad, pad, S - pad - 1, S - pad - 1],
                        radius=round(S * 0.22), fill=BG)

    # A hairline gold frame, composited rather than drawn straight on. ImageDraw
    # REPLACES pixels instead of blending them, so passing an alpha colour to
    # `outline` leaves a half-transparent ring rather than a faint one — which
    # read as a full-strength gold band, exactly the mistake the old periwinkle
    # icon made. Drawn opaque on its own layer and blended at 30%, it does the
    # one job it has: stop the tile dissolving into a dark browser chrome. 0.55 was
    # arrived at by looking: 0.30 vanished, and full strength was the loud band.
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle(
        [pad, pad, S - pad - 1, S - pad - 1], radius=round(S * 0.22),
        outline=GOLD + (255,), width=max(1, round(S * (0.016 if px <= 64 else 0.010))))
    img = Image.alpha_composite(img, Image.blend(
        Image.new("RGBA", (S, S), (0, 0, 0, 0)), ring, 0.55))
    d = ImageDraw.Draw(img)

    # 0.38, not 0.46. At the larger size the pair very nearly touched the frame,
    # and a mark with no air around it reads as cramped at every size.
    #
    # SMALL SIZES GET A HEAVIER, LARGER CUT, and this is the whole reason the
    # script renders per-size instead of downsampling one master. Playfair is a
    # didone: its hairlines are a fifth the width of its stems, and at a 16px
    # favicon those hairlines land on a fraction of a pixel and disappear — the
    # F loses its arms and the pair turns to mud. Looked at on a contact sheet
    # at real size, which is the only way to see it. Weight 700 and a slightly
    # bigger setting keep the thin strokes on the grid; at 192px and up the
    # lighter, more elegant cut is what the wordmark actually looks like.
    # BELOW ~20px, ONE LETTER. Looked at on a contact sheet at 16/24/32/48: at
    # 48 and 32 "FA" is clean, at 24 it is marginal, and at 16 it is mud — two
    # didone capitals cannot survive sixteen pixels, and the previous icon had
    # exactly the same problem. A tab icon's whole job is to be picked out of a
    # strip of twenty tabs, and mud fails it.
    #
    # The letter kept is the gold italic A, not the F: "Atlas" is the accented
    # half of the wordmark, gold-on-dark is the site's colour signature, and the
    # italic lean is distinctive where a roman F is just a serif F.
    if px <= 20:
        f = _weighted(fonts["italic"], round(S * 0.62), 700)
        b = d.textbbox((0, 0), "A", font=f)
        d.text(((S - (b[2] - b[0])) / 2 - b[0], (S - (b[3] - b[1])) / 2 - b[1]),
               "A", font=f, fill=GOLD)
        return img.resize((px, px), Image.LANCZOS)

    size = round(S * (0.42 if small else 0.38))
    weight = 700 if small else 600
    f_roman = _weighted(fonts["roman"], size, weight)
    f_ital = _weighted(fonts["italic"], size, weight)

    # Measured, not guessed: Playfair's italic A has a different advance and
    # bearing from the roman F, so a fixed nudge would sit wrong at some sizes.
    fb, ab = d.textbbox((0, 0), "F", font=f_roman), d.textbbox((0, 0), "A", font=f_ital)
    fw, aw = fb[2] - fb[0], ab[2] - ab[0]
    # Tracking. The italic A leans INTO the F, so the two need more metric gap
    # than they look like they need — at 0.012 they were all but touching.
    gap = round(S * 0.035)
    total = fw + gap + aw
    x = (S - total) / 2
    # Vertically centred on the CAP HEIGHT of the pair rather than on the font's
    # line box, which carries descender space neither letter uses.
    top = min(fb[1], ab[1])
    bot = max(fb[3], ab[3])
    y = (S - (bot - top)) / 2 - top

    d.text((x - fb[0], y), "F", font=f_roman, fill=CREAM)
    d.text((x + fw + gap - ab[0], y), "A", font=f_ital, fill=GOLD)

    return img.resize((px, px), Image.LANCZOS)


def draw_og(fonts: dict[str, Path]) -> Image.Image:
    """The 1200x630 card every shared link shows.

    The one it replaces was from the same dead palette as the old icons —
    periwinkle bars, a blue rule, DejaVu Sans — so a FicAtlas link pasted into
    Discord or a Reddit reply looked like a different site from the one it
    opened. It was also two facts out of date: "19+ million" against an index of
    20.8M, and "FicAlley", which is not what the archive is called anywhere else
    on the site.

    Same lockup as the icon, at reading size: Playfair for the wordmark with
    "Atlas" in gold italic, DM Sans for the line underneath, on the site's own
    ground. No decorative furniture — the old card's floating bars said nothing,
    and a share card is read in a scroll at thumbnail size where anything that is
    not the name or the claim is noise.
    """
    W, H, SS2 = 1200, 630, 2
    img = Image.new("RGB", (W * SS2, H * SS2), BG)
    d = ImageDraw.Draw(img)
    S = SS2

    wordmark = _weighted(fonts["roman"], 132 * S, 600)
    wordital = _weighted(fonts["italic"], 132 * S, 600)
    body = _weighted(fonts["sans"], 40 * S, 400)
    small_f = _weighted(fonts["sans"], 28 * S, 500)

    x, y = 80 * S, 168 * S
    fb = d.textbbox((0, 0), "Fic", font=wordmark)
    d.text((x - fb[0], y), "Fic", font=wordmark, fill=CREAM)
    d.text((x + (fb[2] - fb[0]) + 6 * S, y), "Atlas", font=wordital, fill=GOLD)

    # A gold rule under the wordmark, the width of the wordmark — the site uses
    # the accent as a hairline, never as a block.
    ab = d.textbbox((0, 0), "Atlas", font=wordital)
    rule_w = (fb[2] - fb[0]) + 6 * S + (ab[2] - ab[0])
    ry = y + 190 * S
    d.rectangle([x, ry, x + rule_w, ry + 3 * S], fill=GOLD)

    # "20+ million", deliberately imprecise and deliberately low: this is baked
    # at build time and the index only grows, so a rounded floor stays true for
    # months where an exact figure is wrong the day after it is rendered. The
    # same reasoning layout.tsx gives for the description it mirrors.
    for i, line in enumerate([
            "Search 20+ million fanworks across AO3,",
            "FanFiction.net and FictionAlley at once."]):
        d.text((x, ry + (44 + i * 52) * S), line, font=body, fill=(168, 160, 148))

    # Clear of the body text: at the first spacing the domain sat four pixels
    # under the second line and read as a third line of the sentence.
    d.text((x, H * S - 78 * S), "ficatlas.com", font=small_f, fill=GOLD)
    return img.resize((W, H), Image.LANCZOS)


def write_ico(path: Path, images: list[Image.Image]) -> None:
    """An ICO holding one PNG per size, each its own artwork.

    The container is trivial — a 6-byte header, a 16-byte directory entry per
    image, then the payloads — and writing it directly is the only way to put
    DIFFERENT art at different sizes. Pillow's ICO writer takes a single image
    and resizes it, which would undo the small-size treatment in draw_icon().
    """
    import struct
    blobs = []
    for im in images:
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, "PNG", optimize=True)
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(blobs))     # reserved, type=icon, count
    offset = len(header) + 16 * len(blobs)
    entries, payload = b"", b""
    for im, blob in zip(images, blobs):
        w = 0 if im.width >= 256 else im.width         # 0 means 256 in an ICO
        h = 0 if im.height >= 256 else im.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        payload += blob
    path.write_bytes(header + entries + payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", default=os.path.expanduser("~/.cache/ficatlas-fonts"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "public"))
    a = ap.parse_args()

    fonts = fetch_fonts(Path(a.fonts))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    for name, px in SIZES.items():
        img = draw_icon(px, fonts)
        # RGB, not RGBA: the tile is opaque by design (see the module docstring),
        # and Google's consent screen has rejected PNGs with an alpha channel.
        img.convert("RGB").save(out / name, "PNG", optimize=True)
        print(f"  {name:18} {px}x{px}")

    # The .ico is written by hand rather than with Pillow's `sizes=`, because
    # that downsamples ONE master to every size — which is exactly what this
    # script exists to avoid. layout.tsx declares the ico as 16x16 32x32 48x48,
    # so the 16px entry really is what a browser puts in the tab strip, and it
    # needs the single-letter art that draw_icon() gives it at that size.
    og = draw_og(fonts)
    og.save(out / "og.png", "PNG", optimize=True)
    print(f"  {'og.png':18} 1200x630")

    write_ico(out / "favicon.ico", [draw_icon(n, fonts) for n in (16, 32, 48, 64)])
    print(f"  {'favicon.ico':18} 16/32/48/64, each drawn at its own size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
