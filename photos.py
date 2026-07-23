"""Fetch a player photo from Wikimedia Commons — only when it carries a
reuse license (public domain or Creative Commons) — with attribution.

Returns (image_bytes, credit_line) or None. The caller falls back to the
design-only card when None (no free-licensed photo exists for that player).
"""

import html
import json
import os
import re
import urllib.parse
import urllib.request

_API = "https://en.wikipedia.org/w/api.php"
_UA = "NBANewsBot/1.0 (https://github.com/vonsidy/nba-news-bot; educational)"

# License strings we accept (must permit commercial reuse; attribution ok).
_FREE = ("public domain", "cc0", "cc-by", "cc by", "attribution", "creative commons")

# Shortest source side, in pixels, a photo must have to be used on the card.
# The card renders at 1080px; below this the frame is filled by upscaling, and
# a small gym photo turns to grain. Anything under it is skipped in favour of
# the logo card. 640 keeps the plentiful ~1000px+ Commons shots and rejects the
# genuinely tiny ones without leaving most players logo-only.
MIN_PHOTO_PX = int(os.getenv("MIN_PHOTO_PX") or 640)


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

    Order matters, and it is: a free action shot in the RIGHT uniform, else the
    official headshot, else whatever free photo exists. The headshot is posed
    rather than in-game and looks it — a floating cutout, not a news photo — so
    it is a last resort, reached only when every free photo shows the wrong kit.
    Jett Howard's only free photos are Michigan ones; Luguentz Dort's are a
    college game and a Team Canada match. Those are what the headshot is for.
    """
    return (get_player_photo(name, teams=teams, strict=True)
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
    # A coach at work. Without these the only signals that fired on a coach
    # were the player ones, none of which he ever matches, so every photo of
    # him scored the same and whatever happened to be first won — which is how
    # a firing card ended up showing Mike Budenholzer at a parade in sunglasses.
    ("sideline",), ("coach", "coaching"), ("bench",), ("huddle",), ("timeout",),
)
# Off-duty contexts. Correct person, wrong occasion for a news card: a
# championship parade or a charity appearance is the opposite of "on the job".
_OFFDUTY_WORDS = ("parade", "celebration", "rally", "ceremony", "ring night",
                  "red carpet", "charity", "gala", "award", "premiere",
                  "signing autographs", "visit", "white house")
# "cropped" is deliberately NOT here — it is REWARDED below instead. It used to
# cost -6, which sank every candidate Zion Williamson has (all six are
# "(cropped)"), but on Commons a "(cropped)" derivative exists precisely because
# somebody framed the original on its subject. That is exactly what this card
# wants, and its absence is what let a wide-angle "LeBron James shooting
# basketball" shot from the stands win, with him a speck at half court.
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


_COMMONS = "https://commons.wikimedia.org/w/api.php"


def _commons_files(name: str, limit: int = 20) -> list[str]:
    """Filenames Commons holds for a person, beyond those the article embeds.

    The article's own images are a thin slice: Erik Spoelstra's page carries
    three, Commons has a dozen including several sideline shots, and coaches
    are the worst served because an article usually embeds one portrait. More
    candidates is the only thing that lets the scoring below actually choose.

    Returns [] on any failure — this widens the pool, it is never required.
    """
    try:
        url = _COMMONS + "?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srnamespace": 6,
            "srsearch": f"{name} basketball", "srlimit": limit, "format": "json",
        })
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            hits = json.load(r).get("query", {}).get("search", [])
        return [h["title"].removeprefix("File:") for h in hits]
    except Exception:
        return []


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
    if not story_teams:
        # No team resolved for this story — plenty of items name none. Without
        # a reference there is nothing to contradict, so judging the jersey
        # here would reject every team-labelled photo on no evidence and send
        # the card to a headshot. Absence of information is not evidence of a
        # wrong uniform.
        return False
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
           story_teams: frozenset[str] = frozenset(),
           dims: tuple | None = None) -> float:
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
    s -= 8 * sum(1 for w in _OFFDUTY_WORDS if w in haystack)
    if "crop" in f:
        # +5, not +3: at +3 the good close-up TIED the wide "LeBron James
        # shooting basketball" shot at 11.0 and the winner came down to sort
        # order. A human deliberately cropping to the subject is a stronger
        # signal about framing than any single action word is, so it should
        # break that tie outright rather than merely draw with it.
        #
        # A double crop ("...(cropped)_(cropped).jpg") IS reliably a face
        # cutout, and penalising it does pick a wider photo — but the wider
        # photo is not necessarily better framed. Jaylen Brown's is a shooting
        # action shot with his arms at the top and his face low, so the
        # top-anchored crop cut past his head entirely: worse than the tight
        # crop it replaced. Framing cannot be fixed by choosing differently
        # without knowing WHERE the face is, so this stays a simple bonus.
        s += 5

    # Very wide frames are usually the whole court from the stands, where the
    # player is a speck. The card crops to a square, so a 2:1 source loses half
    # its width and the subject is rarely what survives.
    if dims and dims[1]:
        ar = dims[0] / dims[1]
        if ar >= 1.7:
            s -= 5
        elif ar <= 1.1:
            s += 2      # portrait/square: subject fills the frame
        # Prefer resolution. The card fills 1080px, so a source much smaller
        # than that is upscaled into the grain you can see on a low-res gym
        # photo. Reward bigger sources up to a cap, and dock the genuinely tiny
        # ones, so a sharp photo beats a small one when there is a choice.
        short = min(dims[0], dims[1])
        if short >= 900:
            s += 2
        elif short < MIN_PHOTO_PX:
            s -= 6
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
        # A nudge, not a veto. Age is only a PROXY for the wrong jersey, and
        # _wrong_uniform now measures that directly — so this ranks a newer
        # photo above an older one without ever disqualifying a good in-team
        # action shot for being a few seasons old.
        s -= 0 if age <= 3 else (age - 3) * 1.0
    else:
        s -= 2  # undated — mildly prefer a photo we can place in time
    return s


def get_player_photo(name: str, width: int = 1000, teams=None, strict=False):
    """Return (png_or_jpg_bytes, 'Photo: <artist> / <license>') or None.

    Gathers every photo on the player's Wikipedia page (not just the lead
    image), prefers recent in-uniform action shots via _score, and returns the
    highest-ranked candidate that carries a free license.

    `strict` rejects any candidate that is visibly the wrong uniform (college,
    national team, a former NBA club). get_any_photo makes a strict pass first,
    so a real in-team action shot always wins; only when every free photo shows
    the wrong kit does it settle for the official headshot.
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
        embedded = [im.get("title", "").removeprefix("File:")
                    for im in page.get("images", [])]
        for t in embedded + _commons_files(name):
            tl = t.lower()
            # jpgs only (svg/png are logos, charts, icons), and require the
            # subject's surname in the filename so we never grab a teammate.
            # The guard matters more now that candidates come from a SEARCH:
            # querying Commons for "Mike Budenholzer basketball" also returns
            # "Brett Brown Spurs.JPG" and "San Antonio Spurs Coaching staff.JPG",
            # and putting another man's face on a firing card is far worse than
            # a dull photo of the right one.
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
            "prop": "imageinfo", "iiprop": "extmetadata|url|size",
            "iiurlwidth": width, "format": "json",
        })
        infos: dict[str, dict] = {}
        for p in j2["query"]["pages"].values():
            if p.get("imageinfo"):
                infos[p.get("title", "").removeprefix("File:")] = p["imageinfo"][0]

        def _info_of(f):
            return infos.get(f) or infos.get(f.replace("_", " ")) or {}

        def _meta_of(f):
            return _info_of(f).get("extmetadata", {})

        def _dims_of(f):
            i = _info_of(f)
            return (i.get("width") or 0, i.get("height") or 0)

        story = team_words(teams)
        scores = {f: _score(f, name, lead, _meta_of(f), story_teams=story,
                            dims=_dims_of(f))
                  for f in ranked}
        ranked.sort(key=lambda f: scores[f], reverse=True)

        # 3. Best-ranked candidate with a genuinely free license wins
        for fname in ranked:
            info = infos.get(fname) or infos.get(fname.replace("_", " "))
            if not info:
                continue
            meta = info.get("extmetadata", {})
            if strict and _wrong_uniform(fname, meta, story):
                continue          # let the caller retry with the headshot
            lic = (
                meta.get("LicenseShortName", {}).get("value", "") + " "
                + meta.get("License", {}).get("value", "")
            ).lower()
            if not any(h in lic for h in _FREE):
                continue  # copyrighted / unknown license — do NOT use
            # Resolution floor. A source whose short side is well under the
            # 1080px card looks like grain once it fills the frame — the Ohio
            # State Jae'Sean Tate photo on 2026-07-23 was exactly this. Skip it
            # and let the caller fall back to the clean logo card; a crisp logo
            # beats a smeared upscale. Only the ORIGINAL dimensions count here,
            # not the 1000px thumb Commons renders from a smaller original.
            w, h = info.get("width") or 0, info.get("height") or 0
            if w and h and min(w, h) < MIN_PHOTO_PX:
                print(f"  photo too low-res ({w}x{h}), skipping: {fname[:50]}")
                continue
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
