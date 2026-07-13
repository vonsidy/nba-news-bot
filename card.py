"""Generate original 'BREAKING NEWS' trade graphics for trade tweets.

No copyrighted photos, no team logos, no fabricated reporter names — just
team colors, lighting, and text rendered fresh each time. Safe to post from a
monetized automated account.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

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


def make_trade_card(player: str, to_team: str, from_team: str | None = None,
                    source: str | None = None) -> bytes | None:
    """Render a cinematic BREAKING NEWS trade card. Returns PNG bytes, or None
    if the destination team can't be resolved (caller posts text-only)."""
    to_abbr = resolve_team(to_team)
    if not to_abbr:
        return None
    from_abbr = resolve_team(from_team) if from_team else None

    W, H = 1080, 1080
    _, prim_hex, sec_hex = TEAMS[to_abbr]
    prim = _hex(prim_hex)
    to_name = TEAMS[to_abbr][0]

    # Dark cinematic base + team-color lighting
    img = _vgradient((W, H), (14, 14, 18), (30, 30, 36)).convert("RGBA")
    img = Image.alpha_composite(img, _glow((W, H), (W - 200, 300), 520, prim, 150))
    if from_abbr:
        fprim = _hex(TEAMS[from_abbr][1])
        img = Image.alpha_composite(img, _glow((W, H), (200, 300), 460, fprim, 110))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    # top hairline in team color
    d.rectangle([0, 0, W, 10], fill=_brighten(prim))

    # kicker
    _center(d, W / 2, 150, "T R A D E   A L E R T", _font(40), (200, 205, 215))

    # player name (wrap to 2 lines if needed)
    name = player.upper()
    parts = name.split()
    if len(parts) >= 2 and len(name) > 12:
        mid = len(parts) // 2 + len(parts) % 2
        lines = [" ".join(parts[:mid]), " ".join(parts[mid:])]
    else:
        lines = [name]
    y = 240
    for line in lines:
        f = _fit_font(d, line, W - 140, 132)
        _center(d, W / 2, y, line, f, (255, 255, 255))
        y += f.size + 8

    # route: FROM -> TO with team colors
    y += 30
    route_font = _font(72)
    to_col = _brighten(prim)
    if from_abbr:
        from_col = _brighten(_hex(TEAMS[from_abbr][1]))
        seg = [(from_abbr, from_col), ("  →  ", (235, 235, 235)), (to_abbr, to_col)]
    else:
        seg = [("TO THE ", (235, 235, 235)), (to_name, to_col)]
    total = sum(d.textbbox((0, 0), s, font=route_font)[2] for s, _ in seg)
    x = (W - total) / 2
    for s, col in seg:
        d.text((x, y), s, font=route_font, fill=col)
        x += d.textbbox((0, 0), s, font=route_font)[2]

    # BREAKING NEWS box (red outline, ESPN-style)
    bn_font = _font(96)
    bn = "BREAKING NEWS"
    box = d.textbbox((0, 0), bn, font=bn_font)
    bw = box[2] - box[0]
    bx0, by0 = (W - bw) / 2 - 50, 800
    bx1, by1 = (W + bw) / 2 + 50, 800 + (box[3] - box[1]) + 70
    for i in range(7):  # thick red rounded outline
        d.rounded_rectangle([bx0 - i, by0 - i, bx1 + i, by1 + i], radius=18, outline=(214, 20, 40))
    _center(d, W / 2, by0 + 22, bn, bn_font, (255, 255, 255))

    # honest attribution to the aggregation source (not a fabricated reporter)
    footer = f"via {source.upper()}" if source else "AUTOMATED NEWS BOT"
    _center(d, W / 2, by1 + 26, footer, _font(34), (150, 155, 165))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


if __name__ == "__main__":
    png = make_trade_card("Jaylen Brown", to_team="76ers", from_team="Celtics", source="ESPN")
    with open("sample_card.png", "wb") as f:
        f.write(png)
    print("wrote sample_card.png")
