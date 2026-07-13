"""Generate original 'TRADE ALERT' graphics for trade tweets.

No copyrighted photos, no team logos — just team colors + text, rendered
fresh each time. Safe to post from a monetized automated account.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont

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

# Common name/nickname -> abbr, so we can resolve whatever the LLM emits.
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
    """Map a team name/abbr/nickname to its 3-letter abbr, or None."""
    if not text:
        return None
    t = text.strip().upper()
    if t in TEAMS:
        return t
    return _ALIASES.get(text.strip().lower())


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _text_color(bg):
    # Pick black or white text for contrast against bg (relative luminance).
    r, g, b = bg
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0, 0, 0) if lum > 150 else (255, 255, 255)


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


def _center(draw, cx, y, text, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    w = box[2] - box[0]
    draw.text((cx - w / 2, y), text, font=font, fill=fill)
    return box[3] - box[1]


def make_trade_card(player: str, to_team: str, from_team: str | None = None) -> bytes | None:
    """Render a trade-alert PNG. Returns PNG bytes, or None if the
    destination team can't be resolved (caller then posts text-only)."""
    to_abbr = resolve_team(to_team)
    if not to_abbr:
        return None
    from_abbr = resolve_team(from_team) if from_team else None

    W, H = 1200, 675
    name, prim_hex, sec_hex = TEAMS[to_abbr]
    prim, sec = _hex(prim_hex), _hex(sec_hex)
    fg = _text_color(prim)

    img = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(img)

    # Diagonal accent band in the secondary color
    d.polygon([(0, H), (0, H - 120), (W, H - 260), (W, H)], fill=sec)

    # "BREAKING" pill, top-left
    pill = _font(46)
    label = "BREAKING"
    box = d.textbbox((0, 0), label, font=pill)
    pw, ph = box[2] - box[0], box[3] - box[1]
    d.rounded_rectangle([60, 60, 60 + pw + 60, 60 + ph + 40], radius=14, fill=(206, 17, 65))
    d.text((90, 78), label, font=pill, fill=(255, 255, 255))

    # "TRADE ALERT" kicker
    _center(d, W / 2, 210, "TRADE ALERT", _font(40), fg)

    # Player name — shrink to fit width
    size = 120
    while size > 48:
        f = _font(size)
        box = d.textbbox((0, 0), player.upper(), font=f)
        if box[2] - box[0] <= W - 120:
            break
        size -= 6
    _center(d, W / 2, 275, player.upper(), _font(size), fg)

    # OLD -> NEW line
    route = f"{from_abbr} → {name}" if from_abbr else f"TO THE {name}"
    _center(d, W / 2, 470, route, _font(56), fg)

    # Footer
    _center(d, W / 2, 610, "AUTOMATED NEWS BOT", _font(26), fg)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


if __name__ == "__main__":
    # Quick manual test: render the Jaylen Brown -> 76ers example.
    png = make_trade_card("Jaylen Brown", to_team="76ers", from_team="Celtics")
    with open("sample_card.png", "wb") as f:
        f.write(png)
    print("wrote sample_card.png")
