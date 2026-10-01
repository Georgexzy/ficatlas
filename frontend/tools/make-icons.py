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
# The needle's proportions, shared with app/CompassMark's 32-unit viewBox.
# On a 32-unit grid with the rose tips at radius 15, these are a tip at
# 16 -/+ 12.3 and a half-width of 3.0. Change one and the other stops being the
# same mark.
#
# WHY IT IS NARROWER THAN IT LOOKS LIKE IT SHOULD BE, and shorter than the rose.
# The first shipped needle was 0.28 of the radius — 8.4 units across on a 32-unit
# grid, a quarter of the mark's width — and the complaint was that the logo had
# gone "fat and squat". The cause is occlusion rather than the needle's own
# bulk: the rose's horizontal cardinal points are 2.32 wide where they meet the
# centre, so a needle 4.2 either side buried them completely and the eight-point
# rose stopped reading as a rose, leaving a chunky lozenge with two small side
# points. Narrower restores the rose.
#
# LENGTH is doing the work that width used to do. At 0.82 of the radius the
# needle stops 2.7 units short of the rose's own tips at both ends, so there is
# a visible gap all the way round it — which is what makes it a separate object
# turning over a fixed rose, at a width that is only 20% wider than the cardinal
# it crosses. An earlier iteration solved the same problem with width alone, at
# 0.28, and paid for it by hiding the mark underneath.
#
# What it does NOT rely on: the shading. The needle is differentiated from the
# rose by its lit north half and its 45%-opacity south half, not by its
# silhouette — a gold needle on a gold rose is invisible at any width, which is
# why the south half is shaded in both this and the CSS.
NEEDLE_LEN = 0.82
NEEDLE_HALF = 0.20
# Gold at 45% toward --bg, for the needle's shaded half. Pre-blended because
# these icons are written RGB, not RGBA.
NEEDLE_SHADE = (99, 85, 58)


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


def _kite(d, cx, cy, ux, uy, length, half, gold, cream):
    """One point of the rose: a kite from the centre out along (ux, uy).

    Split down its own axis — the half on one side of the direction vector in
    gold, the other in cream. That is how a cartographer's rose reads as light
    and shadow, and here it is also the wordmark's two colours.

    Built from the direction vector and its perpendicular rather than from
    hand-written coordinates per compass point. The first attempt wrote the four
    diagonals out by hand and got the shoulder signs wrong, which produced
    overlapping shards through the middle of the mark — obvious once rendered
    and invisible in the source.
    """
    px_, py_ = -uy, ux                       # perpendicular
    tip = (cx + ux * length, cy + uy * length)
    right = (cx + px_ * half, cy + py_ * half)
    left = (cx - px_ * half, cy - py_ * half)
    d.polygon([tip, right, (cx, cy)], fill=gold)
    d.polygon([tip, left, (cx, cy)], fill=cream)


def _rose(d, cx, cy, R, gold, cream, minor=True):
    """A cartographer's compass rose, drawn as flat polygons.

    Flat fills, no gradient: the mark has to survive being 16 pixels wide, and a
    gradient at that size is a smudge. The two-tone facets do the same job with
    geometry, which downsamples cleanly.

    The diagonals are drawn FIRST so the cardinals sit on top of them — on a
    real rose the cardinal points are the dominant feature and the intercardinals
    tuck behind them.
    """
    k = 0.7071067811865476
    if minor:
        # Shorter and much narrower than the cardinals. At small sizes they are
        # dropped entirely: they only fill the gaps between the cardinals and
        # turn a clean four-pointed star into a blob.
        for ux, uy in ((k, -k), (k, k), (-k, k), (-k, -k)):
            _kite(d, cx, cy, ux, uy, R * 0.52, R * 0.085, gold, cream)
    for ux, uy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        _kite(d, cx, cy, ux, uy, R, R * 0.155, gold, cream)


def _needle(d, cx, cy, R, gold, cream):
    """The needle over the rose, from the SAME construction as app/CompassMark.

    A compass rose with nothing turning over it is a star. The needle is what
    makes it an instrument, and it is the part the search loader animates while
    a search runs — so it has to be in the icons too, or the tab, the home
    screen and the header stop being the same mark. That drift is the entire
    reason this file exists.

    One long kite through the centre, split into a lit half and a shaded one so
    the direction of travel reads. `NEEDLE_LEN` and `NEEDLE_HALF` below are the
    same proportion as the 32-unit viewBox in CompassMark: 2.4/16 = 0.85 of the
    cardinal length, and the half-width 2.3/16 ≈ 0.144. Drawn last so it sits
    over the rose.
    """
    L = R * NEEDLE_LEN
    hw = R * NEEDLE_HALF
    # A lit half and a SHADED half, north against south — not the rose's
    # left/right split. A needle's job is to say which end is north, and a
    # left/right facet says nothing about that; a light end against a dim one
    # says it at any size. The shaded half is gold pre-blended toward the
    # ground, because Pillow has no opacity here and the icon must stay RGB
    # (Google's consent screen rejects an alpha channel).
    d.polygon([(cx, cy - L), (cx + hw, cy), (cx, cy)], fill=gold)
    d.polygon([(cx, cy - L), (cx - hw, cy), (cx, cy)], fill=gold)
    d.polygon([(cx, cy + L), (cx - hw, cy), (cx, cy)], fill=NEEDLE_SHADE)
    d.polygon([(cx, cy + L), (cx + hw, cy), (cx, cy)], fill=NEEDLE_SHADE)


def draw_icon(px: int, fonts: dict[str, Path]) -> Image.Image:
    """The app icon: a compass rose on the site's dark ground.

    NOT the letters, and NOT a framed tile. The first cut of this was "FA" in
    Playfair inside a gold ring, which is faithful to the wordmark and was
    rejected on sight — the ring was the loudest thing in it whatever the
    opacity, and two didone capitals turn to mud at favicon size however they
    are cut. A mark that only works above 32px is not an icon.

    A compass rose instead, because an atlas is a book of maps and this one
    indexes three archives — the mark says "find your way through it" without a
    single letterform, reads at 16px as a four-pointed star, and is the one
    piece of cartographic furniture that is not already spoken for in
    SiteIcon.tsx (bookmark = AO3, book = FanFiction.net, scroll = FictionAlley).

    No outline. The tile is a plain rounded square of the site's own ground, so
    the mark is the only thing in it.
    """
    S = px * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = round(S * 0.045)
    d.rounded_rectangle([pad, pad, S - pad - 1, S - pad - 1],
                        radius=round(S * 0.22), fill=BG)

    # Bigger at small sizes: with no frame to hold the composition there is no
    # reason to hold the mark back, and at 16px it wants every pixel it can get.
    R = S * (0.40 if px <= 32 else 0.355)
    _rose(d, S / 2, S / 2, R, GOLD, CREAM, minor=px > 32)
    # At 16px the needle is 4px of a 32px tile: it reads as a hairline and adds
    # nothing but mud, so the smallest sizes keep the bare rose — which is what
    # a compass looks like from far enough away to need an icon at all.
    if px >= 32:
        _needle(d, S / 2, S / 2, R, GOLD, CREAM)

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

    # The same compass rose as the icon, balancing the left-aligned text block.
    # Not decoration for its own sake — it is the one thing that makes a shared
    # card and a browser tab read as the same site, which the old card's floating
    # bars never did.
    _rose(d, W * S - 250 * S, H * S / 2, 128 * S, GOLD, CREAM, minor=True)

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
