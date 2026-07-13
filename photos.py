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


def get_player_photo(name: str, width: int = 1000):
    """Return (png_or_jpg_bytes, 'Photo: <artist> / <license>') or None."""
    try:
        # 1. Lead image filename for the player's Wikipedia page
        j = _get_json({
            "action": "query", "titles": name, "prop": "pageimages",
            "piprop": "name", "format": "json", "redirects": 1,
        })
        page = next(iter(j["query"]["pages"].values()))
        fname = page.get("pageimage")
        if not fname:
            return None

        # 2. That file's license + attribution + a scaled URL
        j2 = _get_json({
            "action": "query", "titles": f"File:{fname}", "prop": "imageinfo",
            "iiprop": "extmetadata|url", "iiurlwidth": width, "format": "json",
        })
        info = next(iter(j2["query"]["pages"].values()))["imageinfo"][0]
        meta = info.get("extmetadata", {})

        lic = (
            meta.get("LicenseShortName", {}).get("value", "") + " "
            + meta.get("License", {}).get("value", "")
        ).lower()
        if not any(h in lic for h in _FREE):
            return None  # copyrighted / unknown license — do NOT use

        artist = _strip(meta.get("Artist", {}).get("value", "")) or "Wikimedia Commons"
        if len(artist) > 42:
            artist = artist[:39] + "..."
        licname = _strip(meta.get("LicenseShortName", {}).get("value", "")) or "CC"
        url = info.get("thumburl") or info.get("url")
        return _get_bytes(url), f"Photo: {artist} / {licname}"
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
