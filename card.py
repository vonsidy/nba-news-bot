"""Generate original 'BREAKING NEWS' trade graphics for trade tweets.

No copyrighted photos, no team logos, no fabricated reporter names — just
team colors, lighting, and text rendered fresh each time. Safe to post from a
monetized automated account.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Account brand stamped on every card for attribution as they get reshared.
BRAND = os.getenv("NBA_BOT_BRAND", "@TheNBASignal")

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
        _center(d, W / 2, by1 + 18, f"VIA {source.upper()}", _font(30), (232, 232, 236))
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


def _team_badge(d, cx, cy, r, abbr):
    """A colored roundel 'badge' for a team — official team colors + abbreviation.
    A trademark-safe stand-in for the real (copyrighted) team logo."""
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


def _draw_team_badges(d, W, cy, from_abbr, to_abbr, r=58):
    """Render the from -> to team badges centered on cy (or just the destination
    badge when the origin team isn't known)."""
    if from_abbr:
        aw = 64
        gap = 40
        total = 2 * r + gap + aw + gap + 2 * r
        x0 = W / 2 - total / 2
        _team_badge(d, x0 + r, cy, r, from_abbr)
        ax = x0 + 2 * r + gap
        _arrow(d, ax, cy, aw, (238, 238, 238))
        _team_badge(d, ax + aw + gap + r, cy, r, to_abbr)
    else:
        _team_badge(d, W / 2, cy, r, to_abbr)


def _design_card(player, to_abbr, from_abbr, prim, to_name, source) -> bytes:
    """Photo-free cinematic card: dark base + team-color lighting."""
    W, H = 1080, 1080
    img = _vgradient((W, H), (14, 14, 18), (30, 30, 36)).convert("RGBA")
    img = Image.alpha_composite(img, _glow((W, H), (W - 200, 300), 520, prim, 150))
    if from_abbr:
        img = Image.alpha_composite(img, _glow((W, H), (200, 300), 460, _hex(TEAMS[from_abbr][1]), 110))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 10], fill=_brighten(prim))
    _brand(d, W, 44, (170, 175, 185))
    _center(d, W / 2, 150, "T R A D E   A L E R T", _font(40), (200, 205, 215))

    y = 240
    for line in _wrap_name(player):
        f = _fit_font(d, line, W - 140, 132)
        _center(d, W / 2, y, line, f, (255, 255, 255))
        y += f.size + 8
    _draw_team_badges(d, W, y + 82, from_abbr, to_abbr, r=64)

    by1 = _breaking_box(d, W, 810)
    footer = f"via {source.upper()}" if source else "AUTOMATED NEWS BOT"
    _center(d, W / 2, by1 + 26, footer, _font(34), (150, 155, 165))

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
    _draw_team_badges(d, W, badge_cy, from_abbr, to_abbr, r=badge_r)
    _breaking_box(d, W, breaking_top, source=source)

    # required CC photo attribution, small at the very bottom
    if credit:
        _center(d, W / 2, H - 32, credit, _font(22), (206, 209, 215))

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
