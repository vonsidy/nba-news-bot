"""Generate original 'BREAKING NEWS' trade graphics for trade tweets.

Real team logos are fetched at runtime from ESPN's public CDN (owner's
explicit choice, accepting the trademark exposure) — they are never committed
to the repo. If a logo can't be fetched, or NBA_BOT_LOGOS=false is set, the
card falls back to the original trademark-safe color-roundel badges, so a CDN
hiccup never breaks a post and the safe mode is one env var away.
"""

from __future__ import annotations

import io
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Account brand stamped on every card for attribution as they get reshared.
BRAND = os.getenv("NBA_BOT_BRAND", "@TheNBASignal")

# Kill switch: set NBA_BOT_LOGOS=false to go back to the roundel badges
# (e.g. after a trademark complaint) without touching code.
USE_TEAM_LOGOS = os.getenv("NBA_BOT_LOGOS", "true").strip().lower() != "false"

# abbr -> (display name, primary hex, secondary hex)
TEAMS = {
    "ATL": ("HAWKS", "#E03A3E", "#C1D32F"),
    "BOS": ("CELTICS", "#007A33", "#BA9653"),
    "BKN": ("NETS", "#000000", "#FFFFFF"),
    "CHA": ("HORNETS", "#1D1160", "#00788C"),
    "CHI": ("BULLS", "#CE1141", "#000000"),
    "CLE": ("CAVALIERS", "#860038", "#FDBB30"),
    "DAL": ("MAVERICKS", "#00538C", "#002B5E"),
    "DEN": ("NUGGETS", "#0E2240", "#FEC524"),
    "DET": ("PISTONS", "#C8102E", "#1D42BA"),
    "GSW": ("WARRIORS", "#1D428A", "#FFC72C"),
    "HOU": ("ROCKETS", "#CE1141", "#000000"),
    "IND": ("PACERS", "#002D62", "#FDBB30"),
    "LAC": ("CLIPPERS", "#C8102E", "#1D428A"),
    "LAL": ("LAKERS", "#552583", "#FDB927"),
    "MEM": ("GRIZZLIES", "#5D76A9", "#12173F"),
    "MIA": ("HEAT", "#98002E", "#F9A01B"),
    "MIL": ("BUCKS", "#00471B", "#EEE1C6"),
    "MIN": ("TIMBERWOLVES", "#0C2340", "#236192"),
    "NOP": ("PELICANS", "#0C2340", "#C8102E"),
    "NYK": ("KNICKS", "#006BB6", "#F58426"),
    "OKC": ("THUNDER", "#007AC1", "#EF3B24"),
    "ORL": ("MAGIC", "#0077C0", "#C4CED4"),
    "PHI": ("76ERS", "#006BB6", "#ED174C"),
    "PHX": ("SUNS", "#1D1160", "#E56020"),
    "POR": ("TRAIL BLAZERS", "#E03A3E", "#000000"),
    "SAC": ("KINGS", "#5A2D81", "#63727A"),
    "SAS": ("SPURS", "#C4CED4", "#000000"),
    "TOR": ("RAPTORS", "#CE1141", "#000000"),
    "UTA": ("JAZZ", "#000000", "#F9A01B"),
    "WAS": ("WIZARDS", "#002B5C", "#E31837"),
}

_ALIASES = {
    "atlanta": "ATL", "hawks": "ATL",
    "boston": "BOS", "celtics": "BOS",
    "brooklyn": "BKN", "nets": "BKN",
    "charlotte": "CHA", "hornets": "CHA",
    "chicago": "CHI", "bulls": "CHI",
    "cleveland": "CLE", "cavaliers": "CLE", "cavs": "CLE",
    "dallas": "DAL", "mavericks": "DAL", "mavs": "DAL",
    "denver": "DEN", "nuggets": "DEN",
    "detroit": "DET", "pistons": "DET",
    "golden state": "GSW", "warriors": "GSW", "gs": "GSW",
    "houston": "HOU", "rockets": "HOU",
    "indiana": "IND", "pacers": "IND",
    "la clippers": "LAC", "clippers": "LAC",
    "la lakers": "LAL", "los angeles lakers": "LAL", "lakers": "LAL",
    "memphis": "MEM", "grizzlies": "MEM",
    "miami": "MIA", "heat": "MIA",
    "milwaukee": "MIL", "bucks": "MIL",
    "minnesota": "MIN", "timberwolves": "MIN", "wolves": "MIN",
    "new orleans": "NOP", "pelicans": "NOP", "pels": "NOP",
    "new york": "NYK", "knicks": "NYK",
    "oklahoma city": "OKC", "thunder": "OKC",
    "orlando": "ORL", "magic": "ORL",
    "philadelphia": "PHI", "76ers": "PHI", "sixers": "PHI", "phila": "PHI",
    "phoenix": "PHX", "suns": "PHX",
    "portland": "POR", "trail blazers": "POR", "blazers": "POR",
    "sacramento": "SAC", "kings": "SAC",
    "san antonio": "SAS", "spurs": "SAS",
    "toronto": "TOR", "raptors": "TOR",
    "utah": "UTA", "jazz": "UTA",
    "washington": "WAS", "wizards": "WAS",
}


def resolve_team(text: str) -> str | None:
    if not text:
        return None
    t = text.strip().upper()
    if t in TEAMS:
        return t
    return _ALIASES.get(text.strip().lower())


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _brighten(c, target=140):
    """Lighten a color toward white until it's readable on a dark background."""
    c = list(c)
    while _lum(c) < target:
        c = [min(255, int(v + (255 - v) * 0.25) + 8) for v in c]
        if c == [255, 255, 255]:
            break
    return tuple(c)


_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "assets", "font-bold.ttf"),
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit_font(draw, text, max_w, start, min_size=44, step=6):
    size = start
    while size > min_size:
        f = _font(size)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_w:
            return f
        size -= step
    return _font(min_size)


def _center(draw, cx, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)


CREDIT_BOTTOM_MARGIN = 30   # air between the credit glyphs and the canvas edge
CREDIT_GAP_BELOW_SOURCE = 6  # air between the VIA <source> line and the credit
# Font size for the CC photo attribution. Owner picked it off a rendered
# comparison at 22 -> 17 -> 15; it is deliberately much smaller than the VIA
# line, which is the credit a reader is meant to notice. Change this one number
# to resize it on both the trade card and the score card.
CREDIT_SIZE = 15


def _credit_line(draw, W, H, credit, size, fill, top=None):
    """Draw the CC photo attribution.

    Positioned by MEASURING the text rather than from a fixed y, because
    _center takes y as the TOP of the string: the old `H - 32` with a 22px
    font put the glyph bottoms ~6px from the edge, so the line read as though
    it were cut off. Measuring keeps the gap constant if the font size ever
    changes.

    `top` anchors the line directly under the VIA <source> line so the two
    attribution lines read as one block. Bottom-anchoring both of them was the
    bug: VIA's glyphs ran to y=1032 while the credit started at y=1024, so they
    overlapped. Without `top` the line falls back to sitting
    CREDIT_BOTTOM_MARGIN above the canvas edge."""
    font = _font(size)
    text_h = draw.textbbox((0, 0), credit, font=font)[3]
    y = top if top is not None else H - CREDIT_BOTTOM_MARGIN - text_h
    _center(draw, W / 2, y, credit, font, fill)


def _brand(draw, W, y, fill, shadow=False):
    """Stamp the account handle in the top-right corner."""
    bf = _font(30)
    box = draw.textbbox((0, 0), BRAND, font=bf)
    x = W - (box[2] - box[0]) - 40
    if shadow:
        draw.text((x + 2, y + 2), BRAND, font=bf, fill=(0, 0, 0))
    draw.text((x, y), BRAND, font=bf, fill=fill)


def _vgradient(size, top, bottom):
    w, h = size
    strip = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        strip.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return strip.resize(size)


def _glow(size, center, radius, color, alpha):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(150))


def _cover(im, size, focus_y=0.5):
    """Resize + crop an image to fill `size`. focus_y picks the vertical
    center of the crop (0=top, 0.5=middle) — bias toward the top for portraits
    so faces don't get cut off."""
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = max(tw, int(w * scale)), max(th, int(h * scale))
    im = im.resize((nw, nh))
    x = (nw - tw) // 2
    y = int((nh - th) * focus_y)
    y = max(0, min(y, nh - th))
    return im.crop((x, y, x + tw, y + th))


def _breaking_box(d, W, y_top, source=None):
    """ESPN-style BREAKING NEWS: white text inside a red outline frame, with an
    optional 'VIA <source>' credit line underneath (their 'FROM SHAMS')."""
    bn_font = _font(90)
    bn = "BREAKING NEWS"
    box = d.textbbox((0, 0), bn, font=bn_font)
    bw, th = box[2] - box[0], box[3] - box[1]
    pad_x, pad_y = 48, 30
    bx0, bx1 = (W - bw) / 2 - pad_x, (W + bw) / 2 + pad_x
    by0, by1 = y_top, y_top + th + 2 * pad_y
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=18, outline=(214, 18, 40), width=8)
    # vertically center the text inside the box (offset out the bbox's top gap)
    _center(d, W / 2, by0 + pad_y - box[1], bn, bn_font, (255, 255, 255))
    if source:
        via_font = _font(30)
        via = f"VIA {source.upper()}"
        _center(d, W / 2, by1 + 18, via, via_font, (232, 232, 236))
        # Return the bottom of the VIA line, not of the box — the photo credit
        # tucks directly under it, and anchoring to the box would overlap it.
        return by1 + 18 + d.textbbox((0, 0), via, font=via_font)[3]
    return by1


def _route_segments(from_abbr, to_abbr, to_name, prim):
    to_col = _brighten(prim)
    if from_abbr:
        from_col = _brighten(_hex(TEAMS[from_abbr][1]))
        return [(from_abbr, from_col), ("  →  ", (235, 235, 235)), (to_abbr, to_col)]
    return [("TO THE ", (235, 235, 235)), (to_name, to_col)]


def _wrap_name(name):
    name = name.upper()
    parts = name.split()
    if len(parts) >= 2 and len(name) > 12:
        mid = len(parts) // 2 + len(parts) % 2
        return [" ".join(parts[:mid]), " ".join(parts[mid:])]
    return [name]


def _draw_route(d, W, y, from_abbr, to_abbr, to_name, prim, size=72):
    seg = _route_segments(from_abbr, to_abbr, to_name, prim)
    rf = _font(size)
    total = sum(d.textbbox((0, 0), s, font=rf)[2] for s, _ in seg)
    x = (W - total) / 2
    for s, col in seg:
        d.text((x, y), s, font=rf, fill=col)
        x += d.textbbox((0, 0), s, font=rf)[2]


# ESPN's team-page slugs differ from the standard NBA abbreviations for a few
# teams; everything else is just the abbreviation lowercased.
_LOGO_CDN = "https://a.espncdn.com/i/teamlogos/nba/500/{slug}.png"
_LOGO_SLUGS = {"GSW": "gs", "NOP": "no", "NYK": "ny", "SAS": "sa",
               "UTA": "utah", "WAS": "wsh"}
_logo_cache: dict[str, Image.Image | None] = {}


def _team_logo(abbr):
    """Fetch a team's logo from ESPN's CDN (cached per process). Returns an
    RGBA image or None — callers fall back to the roundel badge on None, so a
    network failure degrades the card instead of killing the post."""
    if not USE_TEAM_LOGOS:
        return None
    if abbr in _logo_cache:
        return _logo_cache[abbr]
    logo = None
    try:
        url = _LOGO_CDN.format(slug=_LOGO_SLUGS.get(abbr, abbr.lower()))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            logo = Image.open(io.BytesIO(r.read())).convert("RGBA")
    except Exception:
        logo = None
    _logo_cache[abbr] = logo
    return logo


def _paste_logo(img, logo, cx, cy, box):
    """Paste a logo centered at (cx, cy), scaled to fit box×box, over a soft
    white halo (blurred silhouette) so dark logo art stays visible against the
    dark photo scrim. The logo's transparent padding is trimmed first, so every
    team's mark renders at the same visual size regardless of how much empty
    space the source PNG carries around it."""
    logo = logo.copy()
    bbox = logo.getbbox()          # tight bounds of the actual (opaque) art
    if bbox:
        logo = logo.crop(bbox)
    logo.thumbnail((box, box), Image.LANCZOS)
    lw, lh = logo.size
    x, y = int(cx - lw / 2), int(cy - lh / 2)
    pad = 30
    halo = Image.new("L", (lw + 2 * pad, lh + 2 * pad), 0)
    halo.paste(logo.getchannel("A"), (pad, pad))
    halo = halo.filter(ImageFilter.GaussianBlur(9)).point(lambda a: int(a * 0.55))
    img.paste((255, 255, 255), (x - pad, y - pad), halo)
    img.paste(logo, (x, y), logo)


def _team_badge(d, cx, cy, r, abbr):
    """A colored roundel 'badge' for a team — official team colors + abbreviation.
    The trademark-safe fallback when the real logo can't be fetched (or logos
    are disabled via NBA_BOT_LOGOS=false)."""
    prim = _hex(TEAMS[abbr][1])
    sec = _hex(TEAMS[abbr][2])
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sec)          # outer ring
    ir = int(r * 0.86)
    d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=prim)     # inner disc
    txt = (255, 255, 255) if _lum(prim) < 150 else (18, 18, 22)
    f = _fit_font(d, abbr, int(ir * 1.5), int(ir * 1.05), min_size=28, step=4)
    tb = d.textbbox((0, 0), abbr, font=f)
    d.text((cx - (tb[2] - tb[0]) / 2 - tb[0], cy - (tb[3] - tb[1]) / 2 - tb[1]),
           abbr, font=f, fill=txt)


def _arrow(d, x, cy, w, color):
    """A right-pointing arrow drawn as shapes (no font glyph needed, so it works
    with display fonts like Anton that lack an arrow character)."""
    hy = w * 0.16                       # half shaft thickness
    d.rectangle([x, cy - hy, x + w * 0.62, cy + hy], fill=color)
    d.polygon([(x + w * 0.5, cy - w * 0.32), (x + w, cy), (x + w * 0.5, cy + w * 0.32)],
              fill=color)


def _draw_team_badges(img, d, W, cy, from_abbr, to_abbr, r=58):
    """Render the from -> to team marks centered on cy (or just the destination
    when the origin team isn't known). Real logo when fetchable, roundel badge
    otherwise."""
    box = int(r * 2.35)  # logo art carries internal padding — draw a bit larger

    def mark(cx, abbr):
        logo = _team_logo(abbr)
        if logo:
            _paste_logo(img, logo, cx, cy, box)
        else:
            _team_badge(d, cx, cy, r, abbr)

    if from_abbr:
        aw = 64
        gap = 40
        total = 2 * r + gap + aw + gap + 2 * r
        x0 = W / 2 - total / 2
        mark(x0 + r, from_abbr)
        ax = x0 + 2 * r + gap
        _arrow(d, ax, cy, aw, (238, 238, 238))
        mark(ax + aw + gap + r, to_abbr)
    else:
        mark(W / 2, to_abbr)


def _design_card(player, to_abbr, from_abbr, prim, to_name, source) -> bytes:
    """Photo-free card for a player with no free-licensed photo (rookies, deep
    bench). The big real team logo(s) carry the visual weight instead of a photo,
    under a soft team-color wash, with the player's name and the BREAKING NEWS
    banner — built to look intentional, not empty."""
    W, H = 1080, 1080
    img = _vgradient((W, H), (13, 13, 17), (26, 27, 33)).convert("RGBA")
    img = Image.alpha_composite(img, _glow((W, H), (W - 170, 360), 560, prim, 165))
    if from_abbr:
        img = Image.alpha_composite(img, _glow((W, H), (170, 360), 500, _hex(TEAMS[from_abbr][1]), 125))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    _brand(d, W, 52, (214, 218, 226))

    # Big real logo(s) as the hero: FROM -> TO when the origin is known, else a
    # single large destination logo. _draw_team_badges falls back to the roundel
    # only if a logo can't be fetched.
    _draw_team_badges(img, d, W, 330, from_abbr, to_abbr, r=112)

    # Player name, sized to fit and vertically centered in the band between the
    # logo and the banner — a two-line name is scaled down so it never collides
    # with the BREAKING NEWS box.
    lines = _wrap_name(player)
    cap = 124 if len(lines) == 1 else 92
    fonts = [_fit_font(d, ln, W - 150, cap, min_size=46) for ln in lines]
    block_h = sum(f.size for f in fonts) + 8 * (len(lines) - 1)
    y = 632 - block_h / 2
    for ln, f in zip(lines, fonts):
        _center(d, W / 2, y, ln, f, (255, 255, 255))
        y += f.size + 8

    _breaking_box(d, W, 824, source=source)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _photo_card(player, to_abbr, from_abbr, prim, to_name, source, photo, credit) -> bytes:
    """Card built on a real (CC/public-domain) player photo with attribution."""
    W, H = 1080, 1080
    # Bias the crop toward the top so the player's face stays in frame
    base = _cover(Image.open(io.BytesIO(photo)).convert("RGB"), (W, H), focus_y=0.15)

    # Darkening scrim — keep the photo bright and full up top, and concentrate
    # the dark wash in the lower third where the name/badges/banner sit.
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(H):
        a = int(22 + 232 * ((y / H) ** 3.1))
        sd.line([(0, y), (W, y)], fill=(8, 8, 12, min(a, 246)))
    img = Image.alpha_composite(base.convert("RGBA"), scrim)
    img = Image.alpha_composite(img, _glow((W, H), (W // 2, H - 120), 520, prim, 95)).convert("RGB")
    d = ImageDraw.Draw(img)

    # (no top accent bar and no TRADE ALERT tag — photo runs full-bleed; the
    # @handle stays as the only top mark)
    _brand(d, W, 52, (235, 238, 245), shadow=True)

    # No player name — the photo identifies them (like the ESPN card). Team
    # badges sit above the ESPN-style BREAKING NEWS banner, near the bottom.
    breaking_top = 838
    badge_r = 58
    badge_gap = 34           # space between the badges and the BREAKING box
    badge_cy = breaking_top - badge_gap - badge_r
    _draw_team_badges(img, d, W, badge_cy, from_abbr, to_abbr, r=badge_r)
    source_bottom = _breaking_box(d, W, breaking_top, source=source)

    # required CC photo attribution, tucked under the VIA <source> line so the
    # two attribution lines read as one block rather than two stray captions
    if credit:
        _credit_line(d, W, H, credit, size=CREDIT_SIZE, fill=(206, 209, 215),
                     top=source_bottom + CREDIT_GAP_BELOW_SOURCE)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _arena_bg(W, H, away_prim, home_prim, seed: str):
    """A procedural 'blurred game photo' backdrop: dark court gradient, warm
    crowd-bokeh lights, and a team-color glow on each side, all heavily blurred.
    Reads like an arena shot without using anyone's copyrighted game photo.
    Deterministic per matchup so re-renders of the same game look identical."""
    import random
    rng = random.Random(seed)
    img = _vgradient((W, H), (12, 12, 16), (28, 26, 32)).convert("RGBA")
    # hardwood hint: a warm band across the lower third
    wood = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wood)
    wd.rectangle([0, int(H * 0.68), W, H], fill=(96, 64, 34, 90))
    img = Image.alpha_composite(img, wood.filter(ImageFilter.GaussianBlur(60)))
    # crowd bokeh: soft warm dots scattered through the upper two thirds
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bokeh)
    for _ in range(90):
        x, y = rng.randint(0, W), rng.randint(0, int(H * 0.66))
        r = rng.randint(6, 26)
        warm = rng.choice([(255, 226, 180), (255, 244, 220), (200, 210, 255)])
        bd.ellipse([x - r, y - r, x + r, y + r], fill=warm + (rng.randint(28, 80),))
    img = Image.alpha_composite(img, bokeh.filter(ImageFilter.GaussianBlur(14)))
    # each team's color bleeding in from its side
    img = Image.alpha_composite(img, _glow((W, H), (180, 430), 430, away_prim, 110))
    img = Image.alpha_composite(img, _glow((W, H), (W - 180, 430), 430, home_prim, 110))
    return img.filter(ImageFilter.GaussianBlur(6)).convert("RGB")


def _photo_bg(photo, W, H, away_prim, home_prim):
    """Blurred real-player photo as the backdrop: fill the frame, blur it so it
    reads as game atmosphere behind the scoreboard, then a dark scrim so the
    white/dimmed score text stays legible, plus a subtle team-color glow from
    each side. Uses the same CC/public-domain Wikimedia photo the trade cards
    use — real NBA players, no copyrighted game photography."""
    base = _cover(Image.open(io.BytesIO(photo)).convert("RGB"), (W, H), focus_y=0.2)
    base = base.filter(ImageFilter.GaussianBlur(11))
    img = base.convert("RGBA")
    # top + bottom darker than the middle so FINAL and the source credit read,
    # with a solid overall wash to hold the score lines
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(H):
        t = abs((y / H) - 0.5) * 2          # 0 at middle -> 1 at top/bottom
        a = int(120 + 110 * (t ** 1.6))
        sd.line([(0, y), (W, y)], fill=(6, 8, 12, min(a, 235)))
    img = Image.alpha_composite(img, scrim)
    img = Image.alpha_composite(img, _glow((W, H), (150, 430), 420, away_prim, 80))
    img = Image.alpha_composite(img, _glow((W, H), (W - 150, 430), 420, home_prim, 80))
    return img.convert("RGB")


def make_score_card(away_team: str, home_team: str, away_score: int,
                    home_score: int, source: str | None = None,
                    photo: bytes | None = None, credit: str | None = None) -> bytes | None:
    """FINAL score card: two broadcast-style score lines (logo, team, score),
    winner bright / loser dimmed. Backdrop is a blurred real-player photo when
    `photo` is supplied (with `credit`), else the procedural blurred arena.
    Returns PNG bytes, or None when either team can't be resolved."""
    away = resolve_team(away_team)
    home = resolve_team(home_team)
    if not away or not home:
        return None
    W, H = 1080, 1080
    away_prim, home_prim = _hex(TEAMS[away][1]), _hex(TEAMS[home][1])
    img = None
    if photo:
        try:
            img = _photo_bg(photo, W, H, away_prim, home_prim)
        except Exception:
            img = None  # bad/undecodable image -> procedural arena
    if img is None:
        credit = None
        img = _arena_bg(W, H, away_prim, home_prim,
                        seed=f"{away}{home}{away_score}{home_score}")
    d = ImageDraw.Draw(img)
    _brand(d, W, 52, (235, 238, 245), shadow=True)

    # FINAL header, ESPN-red underline bar
    fh = _font(120)
    _center(d, W / 2, 96, "FINAL", fh, (255, 255, 255))
    fb = d.textbbox((0, 0), "FINAL", font=fh)
    d.rectangle([W / 2 - 130, 96 + fb[3] + 18, W / 2 + 130, 96 + fb[3] + 30],
                fill=(214, 18, 40))

    # score lines
    rows = [(away, away_score), (home, home_score)]
    row_x0, row_x1 = 120, W - 120
    logo_box, name_x = 150, row_x0 + 190
    score_f = _font(150)
    for i, (abbr, pts) in enumerate(rows):
        cy = 430 + i * 250
        won = pts > rows[1 - i][1]
        col = (255, 255, 255) if won else (148, 153, 163)
        logo = _team_logo(abbr)
        if logo:
            _paste_logo(img, logo, row_x0 + logo_box / 2, cy, logo_box)
        else:
            _team_badge(d, row_x0 + logo_box / 2, cy, 66, abbr)
        sw = d.textbbox((0, 0), str(pts), font=score_f)
        max_name_w = (row_x1 - (sw[2] - sw[0]) - 40) - name_x
        nf = _fit_font(d, TEAMS[abbr][0], max_name_w, 84, min_size=48)
        nb = d.textbbox((0, 0), TEAMS[abbr][0], font=nf)
        d.text((name_x, cy - (nb[3] - nb[1]) / 2 - nb[1]), TEAMS[abbr][0], font=nf, fill=col)
        d.text((row_x1 - (sw[2] - sw[0]) - sw[0], cy - (sw[3] - sw[1]) / 2 - sw[1]),
               str(pts), font=score_f, fill=col)
        if i == 0:
            # thin divider between the two lines
            d.rectangle([row_x0, cy + 125 - 1, row_x1, cy + 125 + 1], fill=(255, 255, 255, 40))

    source_bottom = None
    if source:
        via_font = _font(30)
        via = f"VIA {source.upper()}"
        _center(d, W / 2, H - 90, via, via_font, (200, 204, 212))
        source_bottom = H - 90 + d.textbbox((0, 0), via, font=via_font)[3]
    if credit:
        # Same tuck as the trade card: anchor under VIA when there is one, so
        # the two attribution lines can't drift into each other.
        _credit_line(d, W, H, credit, size=CREDIT_SIZE, fill=(170, 174, 182),
                     top=None if source_bottom is None
                     else source_bottom + CREDIT_GAP_BELOW_SOURCE)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _tile_name(d, name, font_size, box_w):
    """Fit a player's name into a tile, wrapping to two lines if needed."""
    f = _fit_font(d, name, box_w - 12, font_size, min_size=17, step=2)
    if d.textbbox((0, 0), name, font=f)[2] <= box_w - 12:
        return [name], f
    parts = name.split()
    if len(parts) >= 2:
        return [parts[0], " ".join(parts[1:])], _font(max(17, font_size - 6))
    return [name], f


def make_debate_card(title: list[str], players: list) -> bytes | None:
    """Evergreen engagement card: a bold theme title over a grid of player
    tiles (photo + name + team-color accent). `players` is a list of
    (name, abbr, photo_bytes_or_None); tiles without a photo get a team-color
    plate. Returns PNG bytes, or None if fewer than 4 usable players."""
    players = [p for p in players if p and p[0]][:8]
    if len(players) < 4:
        return None
    W, H = 1080, 1080
    n = len(players)
    cols = 4 if n >= 7 else (3 if n >= 5 else n)
    rows = (n + cols - 1) // cols

    img = _vgradient((W, H), (10, 10, 14), (22, 20, 28)).convert("RGBA")
    img = Image.alpha_composite(img, _glow((W, H), (W - 160, 180), 460, (249, 115, 22), 90))
    img = Image.alpha_composite(img, _glow((W, H), (160, 180), 420, (88, 120, 255), 70)).convert("RGB")
    d = ImageDraw.Draw(img)

    _brand(d, W, 40, (210, 214, 222))
    # title (bold, up to 2 lines) top-left-ish, centered
    ty = 44
    for i, line in enumerate(title):
        tf = _fit_font(d, line, W - 120, 108 if i == 0 else 96)
        _center(d, W / 2, ty, line, tf, (255, 255, 255) if i == 0 else (249, 179, 60))
        ty += tf.size + 2
    # red underline accent
    d.rectangle([W / 2 - 150, ty + 6, W / 2 + 150, ty + 16], fill=(214, 18, 40))

    grid_top = ty + 44
    margin, gap = 44, 16
    foot = 58
    tile_w = (W - 2 * margin - gap * (cols - 1)) / cols
    tile_h = (H - grid_top - foot - gap * (rows - 1) - margin) / rows

    for idx, p in enumerate(players):
        name, abbr = p[0], p[1]
        photo = p[2] if len(p) > 2 else None
        r, c = divmod(idx, cols)
        x0 = margin + c * (tile_w + gap)
        y0 = grid_top + r * (tile_h + gap)
        prim = _hex(TEAMS[abbr][1]) if abbr in TEAMS else (60, 60, 70)

        tw_i, th_i = int(tile_w), int(tile_h)
        # team-color base (subtle top->dark gradient) so a cut-out headshot sits
        # ON the player's team color, like a broadcast card
        tile = _vgradient((tw_i, th_i), prim, tuple(int(c * 0.42) for c in prim))
        if photo:
            try:
                im = Image.open(io.BytesIO(photo))
                transparent = im.mode in ("RGBA", "LA") or (
                    im.mode == "P" and "transparency" in im.info)
                if transparent:
                    # official headshot cut-out: scale to tile width, anchor at
                    # the bottom so head+shoulders rise off the team color
                    im = im.convert("RGBA")
                    scale = tw_i / im.width
                    im = im.resize((tw_i, max(1, int(im.height * scale))), Image.LANCZOS)
                    if im.height > th_i:
                        im = im.crop((0, im.height - th_i, tw_i, im.height))
                        oy = 0
                    else:
                        oy = th_i - im.height
                    base = tile.convert("RGBA")
                    base.alpha_composite(im, (0, oy))
                    tile = base.convert("RGB")
                else:
                    tile = _cover(im.convert("RGB"), (tw_i, th_i), focus_y=0.12)
            except Exception:
                pass
        # bottom scrim for the name
        sc = Image.new("RGBA", tile.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sc)
        th = tile.size[1]
        for yy in range(th):
            a = int(235 * max(0, (yy / th - 0.5) / 0.5) ** 1.5)
            sd.line([(0, yy), (tile.size[0], yy)], fill=(6, 8, 12, min(a, 230)))
        tile = Image.alpha_composite(tile.convert("RGBA"), sc).convert("RGB")
        td = ImageDraw.Draw(tile)
        td.rectangle([0, th - 6, tile.size[0], th], fill=_brighten(prim))
        lines, nf = _tile_name(td, name.upper(), 30, tile.size[0])
        ny = th - 16 - len(lines) * (nf.size + 1)
        for ln in lines:
            _center(td, tile.size[0] / 2, ny, ln, nf, (255, 255, 255))
            ny += nf.size + 1
        img.paste(tile, (int(x0), int(y0)))

    _center(d, W / 2, H - 40, "@TheNBASignal  ·  who you got?", _font(28), (150, 155, 165))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def make_trade_card(player: str, to_team: str, from_team: str | None = None,
                    source: str | None = None, photo: bytes | None = None,
                    credit: str | None = None) -> bytes | None:
    """Render a BREAKING NEWS trade card. Uses a real photo when `photo` is
    supplied (with `credit`), else the photo-free design. Returns PNG bytes, or
    None if the destination team can't be resolved (caller posts text-only)."""
    to_abbr = resolve_team(to_team)
    if not to_abbr:
        return None
    from_abbr = resolve_team(from_team) if from_team else None
    prim = _hex(TEAMS[to_abbr][1])
    to_name = TEAMS[to_abbr][0]

    if photo:
        try:
            return _photo_card(player, to_abbr, from_abbr, prim, to_name, source, photo, credit)
        except Exception:
            pass  # bad/undecodable image -> fall back to design card
    return _design_card(player, to_abbr, from_abbr, prim, to_name, source)


if __name__ == "__main__":
    import photos
    res = photos.get_player_photo("Jaylen Brown")
    photo, credit = res if res else (None, None)
    png = make_trade_card("Jaylen Brown", to_team="76ers", from_team="Celtics",
                          source="ESPN", photo=photo, credit=credit)
    with open("sample_card.png", "wb") as f:
        f.write(png)
    print(f"wrote sample_card.png (photo: {'yes' if photo else 'no'})")
