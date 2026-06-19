"""
Renders a battery tray icon with the usage % printed on it, using Pillow.

Designed for legibility at the real Windows tray size (16x16). An earlier
design tried to fit "39%" inside a tall, narrow vertical battery and it became
an unreadable smudge. This version makes the NUMBER the primary signal:

- Horizontal (landscape) battery, so two big digits fit side by side.
- The percent is drawn large and bold, with a dark halo so it stays readable
  on top of the coloured fill at any level.
- Fill LEVEL still shows how much is used; fill COLOUR is the status stride:
    green  < 50%   (plenty)
    amber  50-79%  (getting low)
    red    >= 80%  (almost out)
- Cap + outline   = provider BRAND colour (which provider).
- Unknown state   = empty outline + a single dash (never a fake 0%).
- Error state     = outline with a small X.
- Estimated       = hollow/outlined fill + the "~" already in the tooltip.

We supersample 4x then downscale with LANCZOS so the small digits stay crisp.
"""

from PIL import Image, ImageDraw, ImageFont
from typing import Tuple

# Usage fill colours (status)
COLOR_GREEN  = (  0, 200, 120, 255)
COLOR_AMBER  = (230, 160,   0, 255)
COLOR_RED    = (210,  35,  35, 255)

# Provider brand border colours
BRAND_CLAUDE  = (204, 120,  92, 255)
BRAND_OPENAI  = ( 16, 163, 127, 255)
BRAND_GEMINI  = ( 66, 133, 244, 255)
BRAND_M365    = (  0, 120, 212, 255)
BRAND_DEFAULT = ( 60,  88, 128, 255)

# Other colours
COLOR_TRACK = ( 38,  46,  74, 255)
COLOR_ERROR = (150, 150, 165, 255)
COLOR_DASH  = (150, 160, 185, 255)

_PROVIDER_BRAND = [
    ("Anthropic", BRAND_CLAUDE),
    ("Claude",    BRAND_CLAUDE),
    ("OpenAI",    BRAND_OPENAI),
    ("Gemini",    BRAND_GEMINI),
    ("Google",    BRAND_GEMINI),
    ("Microsoft", BRAND_M365),
    ("Copilot",   BRAND_M365),
]


def brand_color_for(provider_name: str) -> Tuple[int, int, int, int]:
    for keyword, color in _PROVIDER_BRAND:
        if keyword.lower() in provider_name.lower():
            return color
    return BRAND_DEFAULT


def _fill_color(percent: float) -> Tuple[int, int, int, int]:
    # Three status strides:
    #   green  < 50%   (plenty left)
    #   amber  50-79%  (getting low)
    #   red    >= 80%  (almost out)
    if percent < 50:
        return COLOR_GREEN
    if percent < 80:
        return COLOR_AMBER
    return COLOR_RED


def _load_font(px: int):
    """Load a bold TrueType font at the given pixel size, trying a few common
    bundled/system faces. DejaVuSans-Bold ships with Pillow, so this nearly
    always succeeds; the bitmap default is a last resort (it ignores size)."""
    candidates = [
        "DejaVuSans-Bold.ttf",      # bundled with Pillow
        "arialbd.ttf", "segoeuib.ttf", "Arialbd.ttf",
        "Helvetica-Bold.ttf", "LiberationSans-Bold.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _draw_centered_text(draw, box, text, font, fill, halo):
    """Draw text centred in box=(x0,y0,x1,y1) with a dark halo for contrast."""
    x0, y0, x1, y1 = box
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        tx = x0 + (x1 - x0 - tw) / 2 - l
        ty = y0 + (y1 - y0 - th) / 2 - t
    except Exception:
        tx, ty = x0, y0
    # Halo: draw the text offset in 8 directions so digits read on any fill.
    for dx in (-halo, 0, halo):
        for dy in (-halo, 0, halo):
            if dx or dy:
                draw.text((tx + dx, ty + dy), text, font=font, fill=(8, 12, 22, 235))
    draw.text((tx, ty), text, font=font, fill=fill)


def render_tray_icon(
    percent: float,
    is_error: bool = False,
    is_unknown: bool = False,
    estimated: bool = False,
    size: int = 64,
    border_color: Tuple[int, int, int, int] = BRAND_DEFAULT,
) -> Image.Image:
    """Draw a horizontal battery icon with the usage % printed on it."""
    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Landscape battery body so two digits fit. The body takes most of the
    # width; a small cap (terminal) sits on the right.
    cap_w  = max(ss, int(S * 0.06))
    gap    = max(1, int(S * 0.015))
    body_w = int(S * 0.86) - cap_w - gap
    body_h = int(S * 0.60)
    bx = max(1, int(S * 0.06))
    by = (S - body_h) // 2
    radius = max(2, int(S * 0.10))
    stroke = max(ss, int(S * 0.055))

    # Cap on the right edge.
    cap_h = int(body_h * 0.46)
    cap_x = bx + body_w + gap
    cap_y = by + (body_h - cap_h) // 2
    draw.rounded_rectangle(
        [cap_x, cap_y, cap_x + cap_w, cap_y + cap_h],
        radius=max(1, cap_w // 2),
        fill=border_color,
    )

    # Body outline + dark track.
    draw.rounded_rectangle(
        [bx, by, bx + body_w, by + body_h],
        radius=radius,
        fill=COLOR_TRACK,
        outline=border_color,
        width=stroke,
    )

    inset = stroke + max(1, int(S * 0.015))
    ix0 = bx + inset
    iy0 = by + inset
    ix1 = bx + body_w - inset
    iy1 = by + body_h - inset
    inner_w = ix1 - ix0

    if is_error:
        m = int((iy1 - iy0) * 0.18)
        lw = max(ss, int(S * 0.05))
        draw.line([ix0 + m, iy0 + m, ix1 - m, iy1 - m], fill=COLOR_ERROR, width=lw)
        draw.line([ix1 - m, iy0 + m, ix0 + m, iy1 - m], fill=COLOR_ERROR, width=lw)
        return img.resize((size, size), Image.LANCZOS)

    if is_unknown:
        dash_w = int(inner_w * 0.4)
        dash_h = max(ss, int(S * 0.05))
        dcx = (ix0 + ix1) // 2
        dcy = (iy0 + iy1) // 2
        draw.rounded_rectangle(
            [dcx - dash_w // 2, dcy - dash_h // 2, dcx + dash_w // 2, dcy + dash_h // 2],
            radius=dash_h // 2,
            fill=COLOR_DASH,
        )
        return img.resize((size, size), Image.LANCZOS)

    # Fill grows left->right with usage; colour is the status stride.
    pct = max(0.0, min(100.0, float(percent)))
    fill_w = round(inner_w * pct / 100.0)
    color = _fill_color(pct)
    if fill_w > 0:
        fr = max(1, radius - stroke)
        if estimated:
            ew = max(ss, int(S * 0.03))
            draw.rounded_rectangle([ix0, iy0, ix0 + fill_w, iy1], radius=fr,
                                   outline=color, width=ew)
        else:
            draw.rounded_rectangle([ix0, iy0, ix0 + fill_w, iy1], radius=fr, fill=color)

    # The number, large and bold, centred over the body with a dark halo so it
    # stays legible regardless of where the fill edge falls. 100 -> "99+" so
    # three digits never have to squeeze in.
    label = "99+" if pct >= 99.5 else str(int(round(pct)))
    font_px = int(body_h * (0.78 if len(label) <= 2 else 0.62))
    font = _load_font(font_px)
    if font is not None:
        halo = max(ss, int(S * 0.02))
        _draw_centered_text(
            draw,
            (bx + stroke, by + stroke, bx + body_w - stroke, by + body_h - stroke),
            label, font, (255, 255, 255, 255), halo,
        )

    return img.resize((size, size), Image.LANCZOS)


def render_icon_for_usage(usage) -> Image.Image:
    if usage is None:
        return render_tray_icon(0, is_error=True, border_color=BRAND_DEFAULT)
    border = brand_color_for(usage.provider)
    return render_tray_icon(
        percent=usage.percent,
        is_error=getattr(usage, "is_error", False),
        border_color=border,
    )
