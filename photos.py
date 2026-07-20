"""Fetch a player photo from Wikimedia Commons — only when it carries a
reuse license (public domain or Creative Commons) — with attribution.

Returns (image_bytes, credit_line) or None. The caller falls back to the
design-only card when None (no free-licensed photo exists for that player).
"""

import html
import json
import re
import urllib.parse
import urllib.request

_API = "https://en.wikipedia.org/w/api.php"
_UA = "NBANewsBot/1.0 (https://github.com/vonsidy/nba-news-bot; educational)"

# License strings we accept (must permit commercial reuse; attribution ok).
_FREE = ("public domain", "cc0", "cc-by", "cc by", "attribution", "creative commons")


def _get_json(params: dict) -> dict:
    url = _API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# Filename words that suggest an in-game / action shot (preferred — they make a
# far better card than a posed headshot) vs. a static portrait (deprioritized).
_ACTION_WORDS = ("dunk", "shoot", "shooting", "layup", "drive", "driving",
                 "defend", "defending", "dribbl", "game", " vs", "vs.", "vs ",
                 "against", "court", "playing", "warmup", "warm-up", "practice",
                 "free throw", "jump", "rebound")
_BORING_WORDS = ("headshot", "head shot", "portrait", "mugshot", "cropped",
                 "head)", "face")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _last_name(name: str) -> str:
    parts = [p for p in re.findall(r"[a-z]+", (name or "").lower())
             if p not in _SUFFIXES]
    return parts[-1] if parts else ""


def _score(fname: str, name: str, lead: str | None) -> float:
    """Rank a candidate photo filename. Action shots beat portraits; newer
    photos beat old ones; the page's lead image gets a trust bonus (it is the
    canonical photo of the right person)."""
    f = fname.lower()
    s = 0.0
    if lead and fname == lead:
        s += 5
    s += 4 * sum(1 for w in _ACTION_WORDS if w in f)
    s -= 5 * sum(1 for w in _BORING_WORDS if w in f)
    years = [int(y) for y in re.findall(r"(20[0-2]\d)", f)]
    if years:
        s += max(0, (max(years) - 2015)) * 0.5  # recency: newer jersey/team
    return s


def get_player_photo(name: str, width: int = 1000):
    """Return (png_or_jpg_bytes, 'Photo: <artist> / <license>') or None.

    Gathers every photo on the player's Wikipedia page (not just the lead
    image), prefers action/in-game shots via _score, and returns the highest-
    ranked candidate that carries a free license."""
    try:
        # 1. Lead image + all image filenames on the player's page
        j = _get_json({
            "action": "query", "titles": name, "prop": "pageimages|images",
            "piprop": "name", "imlimit": "50", "format": "json", "redirects": 1,
        })
        page = next(iter(j["query"]["pages"].values()))
        lead = page.get("pageimage")
        last = _last_name(name)
        cands = set([lead] if lead else [])
        for im in page.get("images", []):
            t = im.get("title", "").removeprefix("File:")
            tl = t.lower()
            # jpgs only (svg/png are logos, charts, icons), and require the
            # player's surname in the filename so we never grab a teammate.
            if tl.endswith((".jpg", ".jpeg")) and last and last in tl:
                cands.add(t)
        if not cands:
            return None
        ranked = sorted(cands, key=lambda f: _score(f, name, lead), reverse=True)[:8]

        # 2. One batched license/URL lookup for the top candidates
        j2 = _get_json({
            "action": "query", "titles": "|".join(f"File:{f}" for f in ranked),
            "prop": "imageinfo", "iiprop": "extmetadata|url",
            "iiurlwidth": width, "format": "json",
        })
        infos: dict[str, dict] = {}
        for p in j2["query"]["pages"].values():
            if p.get("imageinfo"):
                infos[p.get("title", "").removeprefix("File:")] = p["imageinfo"][0]

        # 3. Best-ranked candidate with a genuinely free license wins
        for fname in ranked:
            info = infos.get(fname) or infos.get(fname.replace("_", " "))
            if not info:
                continue
            meta = info.get("extmetadata", {})
            lic = (
                meta.get("LicenseShortName", {}).get("value", "") + " "
                + meta.get("License", {}).get("value", "")
            ).lower()
            if not any(h in lic for h in _FREE):
                continue  # copyrighted / unknown license — do NOT use
            artist = _strip(meta.get("Artist", {}).get("value", "")) or "Wikimedia Commons"
            if len(artist) > 42:
                artist = artist[:39] + "..."
            licname = _strip(meta.get("LicenseShortName", {}).get("value", "")) or "CC"
            url = info.get("thumburl") or info.get("url")
            return _get_bytes(url), f"Photo: {artist} / {licname}"
        return None
    except Exception:
        return None


if __name__ == "__main__":
    for who in ("Jaylen Brown", "LeBron James", "Nikola Jokic"):
        res = get_player_photo(who)
        if res:
            data, credit = res
            print(f"{who}: {len(data)} bytes — {credit}")
        else:
            print(f"{who}: no free-licensed photo found")
