"""Render the Open Graph preview card.

Shared links used to show as a bare URL — index.html carried no og: tags and
there was no image to point them at. This draws one from the same desktop
picture the site uses, with the current totals on it, so the card tracks the
list instead of going stale the first time a repo is added.

Fonts are the one external dependency. ubuntu-latest ships both DejaVu and
Liberation, but if neither is found the card is left exactly as committed
rather than regenerated badly or failing the build.

The workflow deliberately does NOT stage this file. Every run rewrites it and
upload-pages-artifact packages the whole site/ tree, so the DEPLOYED card always
carries today's totals; the committed copy is only the seed that keeps og:image
from 404ing on a fresh checkout. Adding it to the commit step would push a
~230 kB binary into git history every single day for no benefit.
"""
from __future__ import annotations

import pathlib
import sys

W, H = 1200, 630
ASSETS = pathlib.Path(__file__).resolve().parent.parent / "site" / "assets"
OUT = ASSETS / "og.png"

# Bold first, regular second. Order is preference, not availability.
FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
     "/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
)


def _fonts():
    for bold, regular in FONT_CANDIDATES:
        if pathlib.Path(bold).exists() and pathlib.Path(regular).exists():
            return bold, regular
    return None


def render(apps: int, categories: int, tags: int, stars: int) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        print("og: Pillow missing, keeping the committed card", file=sys.stderr)
        return False

    picked = _fonts()
    if not picked:
        print("og: no usable TrueType font, keeping the committed card", file=sys.stderr)
        return False
    bold_path, regular_path = picked

    source = ASSETS / "wallpaper-light.webp"
    if not source.exists():
        print(f"og: {source.name} missing, keeping the committed card", file=sys.stderr)
        return False

    # Cover-crop the desktop picture to the card's aspect ratio.
    bg = Image.open(source).convert("RGB")
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
    left = (bg.width - W) // 2
    top = (bg.height - H) // 2
    card = bg.crop((left, top, left + W, top + H))

    # A frosted panel, blurred from the picture beneath it so it reads as the
    # same Liquid Glass the site is built out of.
    pad, radius = 64, 34
    box = (pad, pad, W - pad, H - pad)
    panel = card.crop(box).filter(ImageFilter.GaussianBlur(26))
    veil = Image.new("RGB", panel.size, (255, 253, 250))
    panel = Image.blend(panel, veil, 0.74)

    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, panel.size[0] - 1, panel.size[1] - 1],
                                           radius=radius, fill=255)
    card.paste(panel, (pad, pad), mask)

    draw = ImageDraw.Draw(card, "RGBA")
    draw.rounded_rectangle([pad, pad, W - pad - 1, H - pad - 1], radius=radius,
                           outline=(0, 0, 0, 38), width=1)

    title = ImageFont.truetype(bold_path, 62)
    sub = ImageFont.truetype(regular_path, 27)
    stat = ImageFont.truetype(bold_path, 25)
    small = ImageFont.truetype(regular_path, 20)

    x, y = pad + 56, pad + 74
    draw.text((x, y), "awesome macOS", font=title, fill=(24, 24, 27))
    y += 74
    draw.text((x, y), "applications", font=title, fill=(24, 24, 27))
    y += 96
    draw.text((x, y), "Every app in my GitHub star list — tagged,", font=sub, fill=(70, 68, 74))
    y += 38
    draw.text((x, y), "categorised and searchable.", font=sub, fill=(70, 68, 74))

    # Totals sit on the baseline so the card says something true about today.
    y = H - pad - 92
    parts = [f"{apps} apps", f"{categories} categories", f"{tags} tags"]
    if stars:
        parts.append(f"{stars / 1_000_000:.1f}M stars" if stars >= 1_000_000
                     else f"{stars // 1000}k stars")
    cx = x
    for i, part in enumerate(parts):
        if i:
            draw.text((cx, y + 2), "·", font=stat, fill=(150, 146, 150))
            cx += draw.textlength("·", font=stat) + 14
        draw.text((cx, y), part, font=stat, fill=(44, 42, 48))
        cx += draw.textlength(part, font=stat) + 14

    draw.text((x, y + 42), "kiarashs.github.io/awesome-macOS-applications",
              font=small, fill=(126, 122, 128))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    card.save(OUT, "PNG", optimize=True)
    print(f"og: wrote {OUT.name} ({OUT.stat().st_size // 1024} kB)", file=sys.stderr)
    return True


if __name__ == "__main__":
    render(281, 26, 46, 2_968_109)
