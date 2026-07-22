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

# A photo from this season or the few before it still shows the right player on
# (usually) the right team. Older than this and get_any_photo prefers the
# official headshot instead — see the note there. Bump with the calendar.
FRESH_SINCE = 2022

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


# ---- Official headshot fallback (every player, incl. rookies with no free
# Wikimedia photo). ESPN's search returns an NBA athlete id (uid ...l:46~a:<id>)
# and the headshot CDN serves a clean transparent-background portrait for it.
# Same CDN family as the team logos; used only when no free photo exists so
# nobody lands on the bare text card.
_ESPN_SEARCH = "https://site.web.api.espn.com/apis/search/v2?limit=10&query="
_ESPN_HEADSHOT = "https://a.espncdn.com/i/headshots/nba/players/full/{id}.png"


def _espn_athlete_id(name: str) -> str | None:
    try:
        url = _ESPN_SEARCH + urllib.parse.quote(name)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            blob = json.dumps(json.load(r))
        # First NBA athlete (league 46) referenced anywhere in the response.
        m = re.search(r"l:46~a:(\d+)", blob)
        return m.group(1) if m else None
    except Exception:
        return None


def get_headshot(name: str):
    """Return (png_bytes, None) for a player's official NBA headshot, or None.
    No attribution line — it's a headshot, not a CC-licensed photo."""
    aid = _espn_athlete_id(name)
    if not aid:
        return None
    try:
        data = _get_bytes(_ESPN_HEADSHOT.format(id=aid))
        # ESPN serves a tiny silhouette placeholder when it has no headshot;
        # require a real image so we fall through to the design card instead.
        if not data or len(data) < 4000:
            return None
        return data, None
    except Exception:
        return None


def get_any_photo(name: str, teams=None):
    """Best available photo for a player: a free-licensed Wikimedia game shot if
    one exists (preferred — real NBA action), otherwise the official headshot so
    EVERY player gets a photo. Returns (bytes, credit_or_None) or None.

    `teams` is the teams the story involves, used to spot a former-team jersey.
    Also works for coaches and executives — Commons has them, ESPN's athlete
    search does not, so they resolve on the Wikimedia leg only.

    Order matters, and it is: a RECENT free action shot, else the official
    headshot, else whatever old free photo exists. The headshot is posed rather
    than in-game, but it is always the current season and the current uniform —
    which beats a genuine action shot of the player in a jersey he left years
    ago. Luguentz Dort's only free options are a 2019 college game and a Team
    Canada match; the headshot shows him in a Thunder jersey.
    """
    return (get_player_photo(name, teams=teams, min_year=FRESH_SINCE)
            or get_headshot(name)
            or get_player_photo(name, teams=teams))


# Concepts that suggest an in-game action shot (preferred — they make a far
# better card than a posed portrait) vs. a static one (deprioritized).
#
# These are CONCEPTS, one tuple of synonyms each, and a concept scores once no
# matter how many of its spellings match. The flat list this replaced counted
# every spelling separately: " vs", "vs." and "vs " all fired on a single
# "vs.", and "layup" and "action" were each in the list twice. That inflation
# is what put a 2016 "LeBron James vs. Kyrie Irving.jpg" (+8, no year, Cavs
# jersey) ahead of "LeBron James at the 2022 NBA All-Star Game.jpg" (+7.5) —
# the exact stale-uniform photo the owner flagged.
_ACTION_CONCEPTS = (
    ("dunk",), ("shoot", "shooting"), ("layup",), ("drive", "driving"),
    ("defend", "defending"), ("dribbl",), ("game",), (" vs", "vs.", "versus"),
    ("against",), ("court",), ("playing",), ("jump",), ("rebound",),
    ("action",),
)
# "cropped" is deliberately NOT here. It was costing -6, which sank EVERY
# candidate Zion Williamson has (all six are "(cropped)") — but a crop is
# usually a tighter frame on the subject, which is better for this card, not
# worse. It describes the framing, not the occasion.
_BORING_WORDS = ("headshot", "head shot", "portrait", "mugshot",
                 "head)", "face", "presser", "press conference", "interview",
                 "podium", "draft", "combine", "warmup", "warm-up", "practice")
# National-team / non-NBA contexts we do NOT want on an NBA card: a Team USA or
# FIBA jersey is exactly the wrong look (the Curry-in-USA-kit problem). Matched
# as whole words so "usa" can't hit random substrings.
_NATIONAL_RE = re.compile(
    r"\b(usa|u\.?s\.?a|fiba|olympics?|olympia|worldcup|world\s?cup|eurobasket|"
    r"national\s?team|team\s?usa|u1[6789]|world\s?championship|"
    r"pan\s?american|universiade|acc|ncaa|college|high\s?school|"
    # Non-English listings. Commons is multilingual, and stronger recency
    # weighting promoted "2023-08-09 Deutschland gegen Kanada
    # (Basketball-Länderspiel)" — a Team Canada jersey — straight to the top,
    # because every marker in this pattern was English-only.
    r"l[aä]nderspiel|nationalmannschaft|gegen|weltmeisterschaft|"
    r"deutschland|kanada|frankreich|spanien|serbien|"
    r"selecci[oó]n|equipe\s?de\s?france|nazionale)\b"
)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Team nicknames and cities, for spotting a former-team jersey in a filename.
# Sourced from card.TEAMS so the two can't drift; card imports nothing from
# here, so there is no cycle.
try:
    import card as _card
    _TEAM_WORDS = frozenset(
        w for w in (
            [v[0].lower() for v in _card.TEAMS.values()] + list(_card._ALIASES)
        ) if len(w) > 3
    )
except Exception:            # card is optional — photos.py stays importable
    _TEAM_WORDS = frozenset()


def team_words(names) -> frozenset[str]:
    """The filename-matchable words for the teams a story involves."""
    out = set()
    for n in names or []:
        abbr = _card.resolve_team(n) if _TEAM_WORDS else None
        if not abbr:
            continue
        out.add(_card.TEAMS[abbr][0].lower())
        out.update(a for a, v in _card._ALIASES.items() if v == abbr and len(a) > 3)
    return frozenset(out)


def _last_name(name: str) -> str:
    parts = [p for p in re.findall(r"[a-z]+", (name or "").lower())
             if p not in _SUFFIXES]
    return parts[-1] if parts else ""


def _haystack(fname: str, meta: dict | None = None) -> str:
    """Filename plus Commons' own Categories and description, lowercased.

    The filename is whatever the uploader typed; the categories are curated.
    "Jett Howard (cropped).jpg" looks neutral but is categorised "2022-23 NCAA
    Division I men's basketball season", and the 2008 LeBron is categorised
    "2007-08 Cleveland Cavaliers season". Both tests below get much sharper for
    reading them, at no extra API cost — it is already in the response.
    """
    hay = fname.lower()
    for key in ("Categories", "ImageDescription"):
        hay += " " + _strip((meta or {}).get(key, {}).get("value", "")).lower()
    return hay


def _wrong_uniform(fname: str, meta: dict | None,
                   story_teams: frozenset[str]) -> bool:
    """True when the photo visibly is NOT this player in current NBA kit.

    This, not age, is the thing actually worth rejecting. Gating on year alone
    pushed Zion Williamson to a posed headshot even though his 2020 photo is a
    Pelicans action shot and he is still a Pelican — a floating cutout in place
    of a real game photo, which is worse on every axis. A photo is only
    disqualified when something SAYS wrong-jersey: a national-team or college
    listing, or an NBA team this story does not involve.
    """
    hay = _haystack(fname, meta)
    if _NATIONAL_RE.search(hay):
        return True
    named = {t for t in _TEAM_WORDS if re.search(rf"\b{t}\b", hay)}
    return bool(named) and not (named & story_teams)


def _year_of(fname: str, meta: dict | None = None) -> int | None:
    """The year a photo was taken: from the filename, else from EXIF metadata.

    The filename is only sometimes dated. "LeBron James vs. Kyrie Irving
    (23965056038).jpg" carries no year, so filename-only dating scored it as
    neutral rather than as the 2016 photo it is — and neutral was enough to
    win. Commons nearly always knows the real date even when the name doesn't,
    so ask the metadata before giving up.
    """
    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-3]\d)\b", fname)]
    if years:
        return max(years)
    for key in ("DateTimeOriginal", "DateTime"):
        raw = _strip((meta or {}).get(key, {}).get("value", ""))
        m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", raw)
        if m:
            return int(m.group(1))
    return None


def _score(fname: str, name: str, lead: str | None,
           meta: dict | None = None, now_year: int = 2026,
           story_teams: frozenset[str] = frozenset()) -> float:
    """Rank a candidate photo. Recent NBA action shots in the right uniform win.

    Recency is weighted far more heavily than it was (0.5/year, which no
    realistic gap could overcome). A news graphic showing a player in a jersey
    he left years ago is the single most obvious way this account looks
    automated, so age is now a primary term, not a tiebreak.
    """
    f = fname.lower()
    # Commons' own Categories are far more reliable than the filename, which is
    # whatever the uploader typed. "Jett Howard (cropped).jpg" looks neutral but
    # is categorised "2022-23 NCAA Division I men's basketball season", and the
    # 2008 LeBron is categorised "2007-08 Cleveland Cavaliers season". Both the
    # college test and the wrong-jersey test below get much sharper by reading
    # them, at no extra API cost — the metadata is already in the response.
    haystack = _haystack(fname, meta)

    s = 0.0
    if lead and fname == lead:
        s += 2  # trust it's the right player, but don't override a good NBA shot
    # One point per CONCEPT present, not per synonym spelling.
    s += 4 * sum(1 for concept in _ACTION_CONCEPTS if any(w in f for w in concept))
    s -= 6 * sum(1 for w in _BORING_WORDS if w in f)
    if _NATIONAL_RE.search(haystack):
        s -= 12  # a Team USA / FIBA / college shot is the wrong jersey entirely

    # Naming an NBA team the story does NOT involve almost always means a former
    # team: "Anthony Davis Hornets.jpg" on a Wizards story is a decade-old
    # jersey. Penalised hard; naming a team the story DOES involve is a positive
    # signal that the uniform is current.
    named = {t for t in _TEAM_WORDS if re.search(rf"\b{t}\b", haystack)}
    if named:
        if named & story_teams:
            s += 5
        else:
            s -= 10

    year = _year_of(fname, meta)
    if year:
        age = max(0, now_year - year)
        # Flat for ~2 seasons, then a steepening penalty. At 10 years old this
        # is -16, which no amount of action wording can buy back.
        s -= 0 if age <= 2 else (age - 2) * 2.0
    else:
        s -= 3  # undated and unverifiable — prefer a photo we can date
    return s


def get_player_photo(name: str, width: int = 1000, teams=None, min_year=None):
    """Return (png_or_jpg_bytes, 'Photo: <artist> / <license>') or None.

    Gathers every photo on the player's Wikipedia page (not just the lead
    image), prefers recent in-uniform action shots via _score, and returns the
    highest-ranked candidate that carries a free license.

    `min_year` rejects anything older than (or impossible to date to) that year.
    get_any_photo uses it to make a first strict pass, so a genuinely recent
    photo is preferred over the official headshot, and the headshot is preferred
    over a decade-old one.
    """
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
        # Coarse pass on filenames alone, then a SECOND pass once the metadata
        # is in hand. The old code ranked once on filenames and never revisited
        # it, so an undated filename could never be recognised as old however
        # clearly Commons knew its date. Same single API call, just 12 titles
        # instead of 8 so the re-rank has something to choose between.
        ranked = sorted(cands, key=lambda f: _score(f, name, lead),
                        reverse=True)[:12]

        # 2. One batched license/URL/metadata lookup for the top candidates
        j2 = _get_json({
            "action": "query", "titles": "|".join(f"File:{f}" for f in ranked),
            "prop": "imageinfo", "iiprop": "extmetadata|url",
            "iiurlwidth": width, "format": "json",
        })
        infos: dict[str, dict] = {}
        for p in j2["query"]["pages"].values():
            if p.get("imageinfo"):
                infos[p.get("title", "").removeprefix("File:")] = p["imageinfo"][0]

        def _meta_of(f):
            info = infos.get(f) or infos.get(f.replace("_", " "))
            return (info or {}).get("extmetadata", {})

        story = team_words(teams)
        scores = {f: _score(f, name, lead, _meta_of(f), story_teams=story)
                  for f in ranked}
        ranked.sort(key=lambda f: scores[f], reverse=True)

        # 3. Best-ranked candidate with a genuinely free license wins
        for fname in ranked:
            info = infos.get(fname) or infos.get(fname.replace("_", " "))
            if not info:
                continue
            meta = info.get("extmetadata", {})
            if min_year is not None:
                # Strict pass: recent AND not visibly the wrong uniform. The
                # year test alone still returned Jett Howard in a Michigan
                # jersey, because his only free photos are recent COLLEGE ones —
                # dated 2023, categorised NCAA, and the best of a bad set. A
                # negative score means some signal said wrong-jersey, so fall
                # through and let the official headshot take it.
                yr = _year_of(fname, meta)
                if yr is None or yr < min_year or scores.get(fname, 0) < 0:
                    continue      # let the caller retry with the headshot
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
