"""NBA breaking-news bot: watch RSS feeds, compose tweets with Claude,
generate a graphic for trades, and post to X.

Run modes:
  python bot.py          # continuous loop (local dev / always-on host)
  python bot.py --once   # single pass, then exit (used by GitHub Actions cron)
"""

import re
import sys
import time
import unicodedata

import card
import composer
import config
import engage
import insiders
import photos
import sources
import state
import tweeter


# Anything X would turn into a t.co link: an explicit scheme, or a bare domain
# it linkifies on sight. Matching this in a tweet body means paying $0.200 for
# that post instead of $0.015.
_LINKIFIED = re.compile(
    rf"(?i)(https?://\S+|\b[a-z0-9][a-z0-9-]*(?:\.(?:{sources._TLDS}))+\b)"
)
_BARE_DOMAIN = re.compile(
    rf"(?i)\b([a-z0-9][a-z0-9-]*)(?:\.(?:{sources._TLDS}))+\b"
)


def _delink(text: str) -> str:
    """Guarantee the body holds nothing X would turn into a t.co link, so the
    post cannot bill at $0.200 instead of $0.015.

    _publisher_name already defuses the usual cause upstream (a bare-domain
    publisher from Google News), so this is the backstop for the model lifting
    a domain out of a headline or summary. A full url is dropped outright; a
    bare domain keeps its name and loses the tld, so "per roundtable.io" still
    reads as "per Roundtable" rather than losing the attribution entirely."""
    def _name(m):
        n = m.group(1).replace("-", " ")
        return n.upper() if len(n) <= 4 else n[:1].upper() + n[1:]

    out = re.sub(r"(?i)\bhttps?://\S+", "", text)
    out = _BARE_DOMAIN.sub(_name, out)
    out = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"[ \t]+", " ", out)).strip()
    if out != text:
        print(f"  de-linked tweet body (would have billed 13x): {text!r} -> {out!r}")
    return out


# ---- Free prefilter: drop obvious non-news BEFORE paying for a Claude call ----
# Conservative by design: a false negative silently loses a real story, so we
# only drop on POSITIVE junk signals (never merely for lacking a team name — that
# would kill player-only headlines like "LeBron reportedly deciding today").

_JUNK_RE = re.compile(
    r"(?i)\b(promo code|betting|sportsbook|casinos?|parlay|prop bets?|odds\b"
    r"|fantasy (basketball|football|hockey)|where to watch|how to watch"
    r"|live stream|recruiting|preseason schedule|schedule release"
    r"|power rankings?|mock draft|way-too-early|offseason grades"
    r"|reasons why|winners and losers|(top|best) \d+ .*this week"
    r"|summer league (recap|grades|review|takeaways|standings)"
    # Opinion, listicle and roundup framings. Added 2026-07-21 after measuring
    # a live cycle: 61 items reached the paid Claude call and only ~10 posts a
    # day come out the other end. These 12 shapes are never a postable scoop —
    # they are somebody's take, a countdown, or an all-30-teams digest. Rumor
    # and free-agency chatter is deliberately NOT filtered here: the composer
    # wants it, and it is the highest-engagement content the account posts.
    r"|why (can.?t|won.?t|is|are|the|\w+ should)|predicting|prediction"
    r"|superlatives|\bargues?\b|\bproves?\b|looking ahead|open thread"
    r"|youtube gold|biggest storylines|salary.?cap sheet|countdown"
    r"|\bnotes:|\bintel:|every .{0,30}(deal|trade).{0,30}all 30 teams"
    r"|ranking the|grading every|grades for|worst .{0,20}contracts"
    # Retrospectives: a NEW article about OLD news. The bot posted "Hawks
    # acquire Luguentz Dort in trade. Full pick details revealed" on
    # 2026-07-21 from a follow-up piece about a trade it had already covered.
    # The article was fresh; the news was not. is_fresh only knows when the
    # ARTICLE was published, so this has to be caught on wording.
    r"|details revealed|revisiting|look(ing)? back|what it means for"
    r"|full (pick|trade) details|one year (later|ago)|anniversary)\b"
)
_OTHER_SPORT_RE = re.compile(
    r"(?i)\b(NFL|NHL|MLB|MLS|WNBA|cricket|rugby|premier league|la liga|bundesliga"
    r"|euroleague|serie a|formula 1|nascar|pga|ufc|maple leafs|canadiens"
    r"|\bjets\b|yankees|dodgers|red sox|packers|cowboys"
    # Football (pro and college) leaked through constantly — a live cycle
    # carried Mahomes' training camp, Eagles, Duke Football and Giants' Malik
    # Nabers, none of which name a league. Every nickname below is checked
    # against the NBA team list: none collide (Hawks/Seahawks and
    # Kings/anything are kept apart by the word boundaries).
    r"|football|eagles|giants|chiefs|patriots|ravens|steelers|bengals|browns"
    r"|titans|colts|jaguars|texans|broncos|chargers|raiders|seahawks|49ers"
    r"|rams|vikings|lions|bears|buccaneers|falcons|panthers|saints"
    r"|commanders|bills|bucs|mayfield|gmfb)\b"
)
# Team names that belong to exactly one non-NBA league and collide with no NBA
# nickname. Matching one of these ends it — no NBA-signal rescue — because a
# shared CITY is not evidence the story is basketball.
_HARD_OTHER_SPORT_RE = re.compile(
    r"(?i)\b("
    # NFL
    r"dolphins|bengals|steelers|ravens|browns|jaguars|texans|colts|titans"
    r"|broncos|chargers|raiders|seahawks|49ers|niners|buccaneers|commanders"
    r"|patriots|packers|vikings"
    # NHL
    r"|canucks|oilers|flames|sabres|penguins|blackhawks|bruins|canadiens"
    r"|maple leafs|golden knights|red wings|dallas stars|minnesota wild"
    r"|tampa bay lightning|st\.? louis blues|new york rangers"
    # MLB
    r"|white sox|red sox|yankees|dodgers|mets|cubs|braves|astros|phillies"
    r"|orioles|padres|mariners|guardians|brewers|marlins"
    # Soccer
    r"|liverpool|arsenal|chelsea|tottenham|everton|fulham|juventus|barcelona"
    r"|psg|man utd|manchester united|manchester city|carlisle united|real madrid"
    r")\b"
)

# Ambiguous names: real non-NBA teams whose nickname is also an ordinary
# basketball word. "NBA stars", "a wild finish", "lightning-quick" are all
# normal NBA phrasing, so these can only disqualify an item that carries NO
# NBA signal at all. That is what caught "Stars GM Nill hopeful to extend
# Robertson" — a Dallas Stars (NHL) story with no NBA word anywhere in it,
# which posted on 2026-07-22.
_SOFT_OTHER_SPORT_RE = re.compile(
    r"(?i)\b("
    r"stars|wild|lightning|blues|rangers|islanders|devils|flyers|capitals"
    r"|hurricanes|sharks|ducks|avalanche|predators|kraken|senators"
    r"|bills|eagles|cowboys|lions|bears|falcons|saints|rams|chiefs|jets"
    r"|giants|cardinals|panthers|reds|rays|nationals"
    r")\b"
)


# NBA signal = the word "NBA" or any team city/nickname (>=4 chars to avoid noise).
_NBA_TOKENS = sorted(
    {a for a in card._ALIASES if len(a) >= 4} | {t.lower() for t in card.TEAMS},
    key=len, reverse=True,
)
_NBA_RE = re.compile(r"(?i)\b(nba|" + "|".join(re.escape(x) for x in _NBA_TOKENS) + r")\b")
# Cities are shared between leagues ("Miami" is Heat and Dolphins), so a strong
# NBA signal is the word NBA or a team NICKNAME, never a city alone.
_NBA_NICKS = sorted({v[0].lower() for v in card.TEAMS.values()}, key=len, reverse=True)
_NBA_STRONG_RE = re.compile(
    r"(?i)\b(nba|" + "|".join(re.escape(x) for x in _NBA_NICKS) + r")\b")

_TEAM_WORDS = {t.lower() for t in card.TEAMS} | {a.lower() for a in card._ALIASES if len(a) > 3}


# Does the HEADLINE assert something happened? Transactions, roster and staff
# moves, availability, results, and the free-agency chatter the composer
# explicitly wants. Deliberately generous — "reportedly", "in talks",
# "suitors", "decision" all count, because build-up is the account's best
# content. What it excludes is the commentary layer around the news: summer
# league grades, "why X was positive", scout takes, franchise-status columns.
#
# Matched against the TITLE only, not the summary. A summary mentions a signing
# in passing all the time; a title that asserts one is a story.
_EVENT_RE = re.compile(
    r"(?i)("
    r"\bsigns?\b|\bsigned\b|\bsigning\b|\bwaives?\b|\bwaived\b|\bre-?signs?\b"
    r"|\btrades?\b|\btraded\b|\bacquires?\b|\bdealt\b|\bagree[sd]?\b|\bclaims?\b"
    r"|\bextension\b|\bextends?\b|\bbuyout\b|\bdeclines?\b|\bpicks? up\b"
    r"|\breleases?d?\b|\bconverts?\b|\bguarantee\b|\bhires?\b|\bfired\b|\bnamed\b"
    r"|\bparts? ways\b|\bout for\b|\bruled out\b|\binjur(y|ed)\b|\bsuspend"
    r"|\bsurgery\b|free agen|\brumor|\breportedly\b|\bsources? say\b"
    r"|\bdecision\b|\btimetable\b|\bmeeting with\b|\bin talks\b|\bsuitors?\b"
    r"|\bexpected to\b|\bintends? to\b|\bfinalizing\b|\bbeat\b|\bdefeat"
    r"|\bfinal score\b|career-high|triple-double)"
)


# A person's name in a headline: two capitalised words, neither an NBA team.
# The inner class allows a capital mid-word so LeBron, DeAndre, JaVale and
# McGee parse — an [A-Z][a-z]+ pattern silently misses every one of them, and
# missing them is exactly the case this is for.
_NAME_PAIR = re.compile(r"\b([A-Z][a-zA-Z'’]{2,})\s+([A-Z][a-zA-Z'’.]{1,})\b")
# Capitalised pairs that are not people. Title-cased headlines produce a lot of
# these and every one is a chance to merge two unrelated stories.
_NOT_A_NAME = {
    "free agency", "summer league", "trade rumors", "trade rumor", "new york",
    "los angeles", "nba trade", "the nba", "this week", "last season",
    "training camp", "draft pick", "second round", "first round",
}
# No person has one of these as a first OR last name. Enumerating bad PAIRS was
# not enough: "NBA Summer" (from "...After NBA Summer League") is not in the
# pair list, parsed as a person, and collapsed a real "Mavericks Waive..."
# transaction as a duplicate. One generic word anywhere in the pair is enough
# to reject it, which covers the combinations nobody thought to list.
_NOT_NAME_WORDS = {
    "nba", "summer", "league", "free", "agency", "trade", "trades", "rumor",
    "rumors", "draft", "pick", "picks", "round", "season", "camp", "training",
    "news", "report", "reports", "update", "updates", "latest", "sources",
    "decision", "contract", "deal", "deals", "signing", "week", "day", "year",
    "east", "west", "eastern", "western", "conference", "finals", "playoffs",
    "star", "all", "team", "teams", "game", "games", "coach", "front", "office",
    # Prepositions and connectives. Title-cased headlines capitalise these, so
    # "Guard After" and "Ways With" both parsed as people — harmless until two
    # unrelated headlines share one and collapse into each other.
    "after", "with", "from", "over", "into", "amid", "before", "during",
    "while", "about", "against", "between", "among", "under", "through",
    "another", "who", "what", "when", "where", "why", "how", "his", "her",
    # Positions and the adjectives that surround them in roster copy.
    "guard", "forward", "center", "point", "shooting", "power", "small",
    "young", "old", "veteran", "rookie", "former", "next", "first", "last",
    "best", "worst", "new", "big", "top", "two", "three", "one", "way",
    "ways", "part", "parts",
}


# The reporters who break NBA news first. An item sourced to one of them is
# worth more than the same story reprinted by an aggregator, so when the budget
# is tight these go through first.
_INSIDER_RE = re.compile(
    r"(?i)\b(shams(\s+charania)?|charania|woj|wojnarowski|marc\s+stein"
    r"|chris\s+haynes|jake\s+fischer|adrian\s+wojnarowski)\b"
)


def _insider_first(items: list) -> list:
    """Scoops before reprints, recency preserved within each group.

    The hourly pace allows only a few paid calls, and without this an insider
    scoop competes for those slots on equal terms with an aggregator's opinion
    piece that happens to be newer. Same budget, better posts."""
    def rank(item) -> int:
        # A tweet READ FROM the insider outranks an article that merely quotes
        # one: it is the same scoop minutes earlier, and it is already paid for.
        if item.source.startswith("@"):
            return 0
        return 1 if _INSIDER_RE.search(f"{item.title} {item.summary}") else 2

    ranked = sorted(enumerate(items), key=lambda p: (rank(p[1]), p[0]))
    return [i for _, i in ranked]


def _headline_names(title: str) -> set[str]:
    """People a headline is about, normalised. Empty when it names nobody."""
    out = set()
    for m in _NAME_PAIR.finditer(title):
        a, b = m.group(1).lower(), m.group(2).lower().rstrip("'’.")
        if a in _TEAM_WORDS or b in _TEAM_WORDS:
            continue
        if a in _NOT_NAME_WORDS or b in _NOT_NAME_WORDS:
            continue
        pair = _deaccent(f"{a} {b}")
        if pair in _NOT_A_NAME:
            continue
        out.add(pair)
    return out


def _collapse_same_story(items: list) -> list:
    """One item per person per cycle, chosen before anything is paid for.

    A single story arrives from a dozen outlets with a dozen different
    headlines, so content_key (a bag of words) does not collapse them: one
    measured cycle carried EIGHT separate articles on the same Rich Paul /
    LeBron story, none of which shared enough wording to dedup. Every one of
    them would have been a paid call, and MAX_POSTS_PER_PLAYER means at most
    one could ever have been published.

    Items arrive newest-first, so the survivor is the freshest article about
    each story. Headlines naming nobody are never merged — a name is the only
    evidence used, and no name means no evidence."""
    kept, claimed = [], {}
    for item in items:
        names = _headline_names(item.title)
        hit = next((n for n in names if n in claimed), None)
        if hit:
            print(f"  same story as an earlier item this cycle ({hit}), "
                  f"not composing: {item.title[:56]}")
            continue
        for n in names:
            claimed[n] = item
        kept.append(item)
    return kept


_SOURCE_RE = re.compile(
    r"(?:\b(?:per|via|according to)\b|\bsources?\b|\breports?\b|@\w)", re.I)


# Case folded on the preposition only — the NAME must stay case-sensitive so
# the capitals are what mark where it ends. A dot is deliberately NOT part of a
# word: "per The Stein Line. Dort enters..." must stop at "Line", and it did
# not when the class included one — it swallowed the full stop and took the
# first word of the next sentence with it. A trailing lowercase TLD is allowed
# back in so "Sportsnet.ca" survives.
_WORD = r"[A-Z][\w'’&-]*(?:\.[a-z]{2,4})?"
_REPORTED_BY_RE = re.compile(
    r"\b(?:[Pp]er|[Vv]ia|[Aa]ccording to)\s+"
    rf"(@\w+|{_WORD}(?:\s+{_WORD}){{0,3}})")


def _reported_by(text: str, fallback: str) -> str:
    """Who the TWEET credits, falling back to the feed that carried the item.

    item.source is the feed, and the feed is often an aggregator: RealGM's
    wiretap republishes other outlets and credits them, so a story Marc Stein
    broke arrives on the RealGM feed. The tweet said "per The Stein Line" while
    the card stamped VIA REALGM underneath it — one post, two different
    sources, and the card crediting the middleman over the reporter.

    The tweet's attribution is the better of the two: the model read the
    article and named who actually reported it."""
    m = _REPORTED_BY_RE.search(text)
    if not m:
        return fallback
    return m.group(1).strip(" .,:;'\"") or fallback


def _names_a_source(text: str) -> bool:
    """True if the tweet credits somebody for the claim.

    Deliberately generous — "per ESPN", "via HoopsHype", "sources tell", "Shams
    reports", "@ShamsCharania" all pass. The point is not to police wording but
    to catch the tweet that asserts a transaction with no reporter behind it at
    all, which is what both stale trades looked like."""
    return bool(_SOURCE_RE.search(text))


def _covered_subject_in(title: str) -> str | None:
    """The name of a player already posted about today that this headline is
    about, or None.

    Requires EVERY token of the stored name to appear, so "LeBron James" needs
    both "LeBron" and "James" present — a lone "James" will not match James
    Harden. Tokens need not be adjacent: 'Rich Paul Addresses LeBron Narrative
    That James Loves the Attention' is the same story and should collapse.
    Accents are folded via _deaccent because feeds spell Doncic several
    different ways."""
    hay = _deaccent(title).lower()
    for name in state.posted_names_today():
        toks = [t for t in re.findall(r"[a-z0-9]+", _deaccent(name).lower()) if len(t) > 2]
        if toks and all(re.search(rf"\b{re.escape(t)}\b", hay) for t in toks):
            return name
    return None


def _worth_composing(item: sources.NewsItem) -> bool:
    """True if the item is worth a paid Claude call.

    This filter is FREE and the Claude call is not, so it is where the bill is
    actually decided. Measured on a live cycle: 61 items were reaching the paid
    call and the account publishes ~10/day — roughly 47 paid reads per tweet.

    The event gate inverts the old 'when in doubt, let it through' rule, which
    was the right default when calls were assumed free and the wrong one at
    $0.0025 each. Set REQUIRE_NEWS_EVENT=0 to restore it when there is budget
    to be generous again."""
    t = f"{item.title} {item.summary}"
    if _JUNK_RE.search(t):
        return False
    # An unambiguous other-league team name is disqualifying on its own. The
    # rule used to be "other-sport AND no NBA signal", which let "Miami
    # Dolphins" through because Miami is also an NBA city — the NBA check
    # rescued an NFL story. Same trap for New York, LA, Chicago, Boston.
    # On 2026-07-22 that cost paid Claude calls on an NFL extension, an MLB
    # injury and an English soccer signing inside one 14-item batch.
    if _HARD_OTHER_SPORT_RE.search(t):
        return False
    # POSITIVE PROOF REQUIRED. This is an NBA account, so an item must show NBA
    # evidence — the word "NBA", or an NBA team city or nickname, anywhere in
    # the title or summary. No evidence, no post.
    #
    # It was a blocklist until 2026-07-22 and it leaked three times in one day:
    # an NFL extension (Dolphins), a fused regex join that silently disabled two
    # leagues, and finally a Dallas Stars NHL story that reached the timeline.
    # Each fix enumerated the league that had just leaked, which is the wrong
    # shape for a list whose job is the league that has not leaked yet. There is
    # no end to that list — Italian basketball and a Milan soccer transfer were
    # both in the same live sample.
    #
    # Measured on 49 live items: 45 carry NBA signal, 4 do not, and 3 of those 4
    # were other leagues. The cost is the 4th — "2 Teams Listed As Suitors For
    # Bradley Beal", real NBA news naming no team and never saying NBA. One miss
    # in 49 to close the category permanently is the right trade for an account
    # whose entire premise is being NBA-only.
    if not _NBA_RE.search(t):
        return False
    # Kept as a second gate: an item can name an NBA city and still be another
    # league's story ("Miami Dolphins", "New York Rangers").
    if (_OTHER_SPORT_RE.search(t) or _HARD_OTHER_SPORT_RE.search(t)
            or _SOFT_OTHER_SPORT_RE.search(t)):
        if not _NBA_STRONG_RE.search(t):
            return False
    # The event gate is exempted for insider tweets, on purpose.
    #
    # _EVENT_RE was tuned against ARTICLE HEADLINES, which are written to
    # announce ("Lakers sign X", "Report: Y traded to Z"). Insiders tweet in
    # prose — "X is headed to the Lakers", "Y has informed the team he intends
    # to test the market" — and half of those phrasings match nothing in the
    # regex. Dropping one would waste the $0.005 already spent reading it AND
    # lose the scoop the account exists to break.
    #
    # The junk and other-sport filters above still apply, and Claude still gets
    # the final say on newsworthiness at $0.00048 — which is a twentieth of what
    # the read cost. Paying it to be sure is the right trade.
    if item.source.startswith("@"):
        return True
    if config.REQUIRE_NEWS_EVENT and not _EVENT_RE.search(item.title):
        return False
    return True


def _et_hour() -> int:
    """Current hour in US Eastern (where NBA Twitter peaks in the evening)."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).hour
    except Exception:
        from datetime import timezone
        return (datetime.now(timezone.utc).hour - 4) % 24  # rough EDT fallback


def maybe_post_engagement() -> None:
    """Post one evergreen debate card per day, in the evening ET peak window —
    keeps the feed alive on slow news days and drives replies. Capped at one a
    day via a Redis day-key; separate from the news post cap.

    Disabled by default (config.ENABLE_DEBATE_POSTS); the owner found these read
    as filler and weren't earning the replies they exist for."""
    if not config.ENABLE_DEBATE_POSTS:
        return
    day = state.today_key()
    if state.is_seen(f"engage:{day}"):
        return
    if _et_hour() < 18:  # hold until 6pm ET so it lands in prime time
        return
    post = engage.pick_daily(day)
    players = []
    for name, abbr in post["players"]:
        # official current-NBA-team headshot (clean cut-out) so every tile is the
        # player in the RIGHT jersey — not a random Wikimedia/national-team shot
        res = photos.get_headshot(name)
        players.append((name, abbr, res[0] if res else None))
    if sum(1 for p in players if p[2]) < 4:
        print("  engagement: fewer than 4 player photos available; skipping today")
        return
    image = card.make_debate_card(post["title"], players)
    if image and tweeter.post(post["caption"], image=image):
        state.mark_seen(f"engage:{day}")
        state.record_post(post["caption"], "debate", True)
        print(f"  posted daily debate card: {' '.join(post['title'])}")


# Generational suffixes aren't part of the identifying name.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Letters that don't decompose under NFKD (so combining-mark stripping misses
# them) — mapped to their plain-ASCII equivalent by hand.
_TRANSLIT = str.maketrans({
    "ø": "o", "đ": "d", "ð": "d", "ł": "l", "ħ": "h", "ı": "i",
    "ß": "s", "æ": "a", "œ": "o", "þ": "t",
})


def _deaccent(s: str) -> str:
    """Fold accents to plain ASCII so 'Jokić' and 'Jokic', 'Dončić' and
    'Doncic' become the same — different outlets spell them both ways."""
    s = (s or "").translate(_TRANSLIT)
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _player_key(name: str) -> str:
    """A stable per-player key that survives name-form AND accent differences
    between outlets. Keyed on first initial + last name, so "Lu Dort" and
    "Luguentz Dort" collapse (l+dort), and "Luka Dončić" and "Luka Doncic"
    collapse (l+doncic), while Jrue and Aaron Holiday stay distinct. Suffixes
    like Jr./III are dropped so "Jaren Jackson Jr." and "Jaren Jackson" match."""
    parts = [p for p in re.sub(r"[^a-z ]+", " ", _deaccent(name).lower()).split()
             if p and p not in _SUFFIXES]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0][0] + parts[-1]


def _event_signature(result: dict) -> str | None:
    """Highlight dedup: one standout-performance post per player per day.
    (Confirmed TRADES are deduped separately by the persistent flags below —
    they must not resurface across day boundaries. Rumors/reports are NOT
    capped here, so ongoing chatter about a player can keep posting.)"""
    if result.get("category") == "highlight" or result.get("is_highlight"):
        player = _player_key(result.get("player"))
        if player:
            return f"hl:{player}:{state.today_key()}"
    return None


def _final_signature(result: dict) -> str | None:
    """One key per game per day, whichever team the headline led with — the
    matchup is sorted so 'Hawks lose to Wizards' and 'Wizards beat Hawks'
    collapse to the same event."""
    a = card.resolve_team(result.get("away_team") or "")
    h = card.resolve_team(result.get("home_team") or "")
    if not a or not h:
        return None
    return f"final:{':'.join(sorted((a, h)))}:{state.today_key()}"


def _trade_team_set(result: dict, title: str) -> set:
    """Every NBA team this trade touches, resolved from the structured fields
    AND scanned out of the headline (so a 3-team deal is recognized as ONE deal
    however an article frames it). Used to post a blockbuster once, not once per
    player it moves."""
    teams = set()
    for f in (result.get("to_team"), result.get("from_team")):
        a = card.resolve_team(f or "")
        if a:
            teams.add(a)
    toks = re.findall(r"[A-Za-z.'-]+", title or "")
    for i, w in enumerate(toks):
        a = card.resolve_team(w)
        if a:
            teams.add(a)
        if i + 1 < len(toks):
            a2 = card.resolve_team(f"{w} {toks[i + 1]}")
            if a2:
                teams.add(a2)
    return teams


# A confirmed trade is deduped for this long — DAYS, not until midnight — so
# follow-up articles that keep a fresh timestamp can't re-post the same deal a
# day later. Auto-expires so the same teams can trade again down the line.
# How long a posted move stays "already covered". Was 6 days, which expired
# before the news cycle around a trade did: on 2026-07-21 the bot posted
# "Hawks acquire Luguentz Dort in trade. Full pick details revealed" — a
# follow-up article about a trade it had already covered more than six days
# earlier, republished as if it were breaking. Outlets keep writing pick-detail
# and grades pieces for weeks, so the flag has to outlive that tail. A player
# genuinely moving twice inside three weeks is rare enough to be worth the
# trade, and the daily per-subject cap still bounds it if it happens.
_TRADE_TTL = 21 * 24 * 3600


def _trade_player_flag(result: dict) -> str | None:
    """Persistent per-player-move key: this player moved, so post it once.

    Deliberately NOT keyed on the destination. Outlets fill to_team differently
    for the same event — "LAL" / "Lakers" / empty for a free agent who "chooses"
    a team — and a destination-keyed flag silently fails to collapse those: the
    `dest or 'x'` fallback used to produce a DIFFERENT key whenever to_team came
    back empty, so the same signing posted again. A player moves once; that's the
    whole key."""
    p = _player_key(_resolve_partial_name(result.get("player") or ""))
    return f"moved:{p}" if p else None


# Containment threshold. 0.60 sits in the gap measured on real posts: the
# duplicate that shipped scores 0.67, a different player on the same team 0.40.
_DUPLICATE_SIMILARITY = 0.60


def _too_similar_to_recent(text: str) -> str | None:
    """The already-posted tweet this one is a near-copy of, or None.

    LAST-DITCH backstop, and deliberately the dumbest check in the file: it
    compares the FINISHED TWEET against the tweets already sent, on words alone.

    Every other duplicate guard reasons about structure — a headline signature,
    a player key, a team set — and each one has now failed in production for its
    own separate reason. content_key does not collapse "...to Warriors" against
    "...to Warriors, per ESPN", because the outlet name changes the signature.
    The per-player backstop did not collapse "Jamarion Sharp" against "Sharp",
    because the keys differed. Both shipped a duplicate to the timeline.

    Those are different bugs with one shape: the guard ran on an INPUT, and the
    inputs were worded differently. This runs on the OUTPUT, where the bot has
    already normalised both stories into its own voice — so two tellings of one
    signing come out nearly the same sentence even when nothing upstream
    matched. It cannot know WHY they are duplicates, which is the point: it
    needs no theory of what went wrong to catch it.

    Containment, not Jaccard: shared words over the SHORTER tweet's word count.
    Jaccard was tried first and cannot separate these — it divides by the union,
    so a terse duplicate of a detailed tweet is punished for the detail it
    omits. The real pair scored 0.286 against 0.222 for a DIFFERENT player on
    the same team, which is not a gap you can put a threshold in.

    Containment asks the question that actually matters — "is this mostly stuff
    I already said?" — and separates cleanly on the same examples:

        duplicate that shipped        0.67   blocked
        same signing, reworded        0.86   blocked
        different player, other team  0.50   posts
        different player, same team   0.40   posts
        different news, same team     0.25   posts
        unrelated trade               0.00   posts
    """
    def bag(s: str) -> set:
        toks = re.findall(r"[a-z0-9]+", _deaccent(s).lower())
        return {w for w in toks if len(w) > 2 and w not in sources._STOP}

    new = bag(text)
    if len(new) < 4:  # too short to judge; let the other guards decide
        return None
    for prev in state.recent_posts(20):
        old_text = prev.get("text") if isinstance(prev, dict) else str(prev)
        old = bag(old_text or "")
        if not old:
            continue
        if len(new & old) / min(len(new), len(old)) >= _DUPLICATE_SIMILARITY:
            return old_text
    return None


def _resolve_partial_name(name: str) -> str:
    """Expand a bare surname to a full name already posted about today.

    The composer returns whatever the article called the player, and articles
    are inconsistent: one wrote "Jamarion Sharp", the next "Summer leaguer
    Sharp". _player_key keys on first-initial + surname, so those became
    'jsharp' and 'sharp' — different subjects, and the same signing posted
    twice 57 minutes apart on 2026-07-21.

    Widening _player_key to surname-only would fix it and break something
    worse: Jrue and Aaron Holiday would collapse into one player. So this stays
    narrow — a SINGLE-token name is expanded only when exactly one name posted
    today ends with it. Two Holidays posted today means no match and no merge,
    which is the correct answer rather than a guess.
    """
    parts = [p for p in re.sub(r"[^a-z ]+", " ", _deaccent(name).lower()).split()
             if p and p not in _SUFFIXES]
    if len(parts) != 1:
        return name
    surname = parts[0]
    matches = {
        n for n in state.posted_names_today()
        if (t := [p for p in re.sub(r"[^a-z ]+", " ", _deaccent(n).lower()).split()
                  if p and p not in _SUFFIXES]) and len(t) > 1 and t[-1] == surname
    }
    if len(matches) == 1:
        full = matches.pop()
        print(f"  resolved partial name {name!r} -> {full!r} (posted today)")
        return full
    return name


def _subject_key(result: dict, title: str) -> str:
    """The thing a story is *about*, for the daily backstop.

    Prefers the player, but falls back to the teams involved so news with no
    player in it is bounded too — coaching hires, front-office moves, cap and
    roster mechanics. The composer leaves `player` empty whenever an item isn't
    centred on one player, so without this fallback that whole class of story had
    no duplicate protection at all: five outlets covering one coaching hire meant
    five posts, exactly the shape of the Thybulle failure.

    Player and team keys live in separate namespaces, so a team's player news and
    its coaching news never compete for the same daily allowance."""
    p = _player_key(_resolve_partial_name(result.get("player") or ""))
    if p:
        return f"p:{p}"
    teams = _trade_team_set(result, title)
    return "t:" + "+".join(sorted(teams)) if teams else ""


def _is_player_move(result: dict) -> bool:
    """Is this item about a specific player changing teams? Broader than the
    model's is_trade flag on purpose — a free-agent signing arrives as "chooses
    Lakers in free agency", "signs one-year deal", and "adds Thybulle, reaches 16
    guaranteed contracts" from four outlets, and only some of those come back
    with is_trade set. Any of them naming a player and a destination is the same
    event and must dedup against it.

    EITHER endpoint counts, not just the destination. Outlets report the same
    trade from whichever end they know — "Johnson dealt in Nets-Nuggets swap"
    gives a from_team and no to_team, and files as a rumor, so a
    destination-only rule left it undetected. Same-day that was invisible,
    because the per-player backstop caught it anyway; the day after, the
    backstop resets and the item posted a second time about a player whose
    move was already reported. The schema defines from_team as the team the
    player is LEAVING, so it never fills for a story that isn't a move."""
    return bool(
        result.get("is_trade")
        or (result.get("player") and (result.get("to_team") or result.get("from_team")))
    )


def _trade_already_posted(teams: set, player_flag: str | None) -> bool:
    """True if this exact player-move already posted, OR a deal sharing >=2 teams
    already posted in the last few days (same blockbuster, different player /
    wording / outlet / day). Persistent, so a trade never resurfaces."""
    if player_flag and state.get_flag(player_flag):
        return True
    return sum(1 for t in teams if state.get_flag(f"tt:{t}")) >= 2


def _mark_trade_posted(teams: set, player_flag: str | None) -> None:
    for t in teams:
        state.set_flag(f"tt:{t}", _TRADE_TTL)
    if player_flag:
        state.set_flag(player_flag, _TRADE_TTL)

# Windows consoles default to cp1252, which crashes on emoji in headlines/tweets
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _affordable(items: list) -> list:
    """The prefix of `items` this cycle may pay to compose.

    The spend ceiling lives here rather than beside the call because composing
    is now batched: the decision is "how many of these can we afford", made
    once, instead of "can we afford this one" asked N times.

    Crucially, items this trims are NOT marked seen — they come back on a later
    cycle when the hour rolls over. That is the difference between pacing the
    budget and silently dropping the news that broke while the budget was spent.
    """
    if not config.MAX_CLAUDE_ITEMS_PER_DAY:
        return items
    day_left = config.MAX_CLAUDE_ITEMS_PER_DAY - state.claude_calls_today()
    if day_left <= 0:
        # The counters are keyed on the ET date (state._today), not UTC — the
        # message said UTC midnight, which is 8pm ET and the wrong answer to
        # "when does it come back".
        print(f"  Claude daily budget spent ({config.MAX_CLAUDE_ITEMS_PER_DAY} items), "
              f"holding until midnight ET")
        return []
    # Keep the evening's share for the evening. The hourly pace below stops one
    # cycle emptying the day, but nothing stopped the MORNING emptying it —
    # see config.CLAUDE_EVENING_RESERVE. Same money, spent when anyone is
    # reading.
    if _et_hour() < config.CLAUDE_EVENING_HOUR:
        spendable = day_left - config.CLAUDE_EVENING_RESERVE
        if spendable <= 0:
            print(f"  {day_left} item(s) left are the evening reserve; holding "
                  f"until {config.CLAUDE_EVENING_HOUR}:00 ET")
            return []
    else:
        spendable = day_left
    # Pace it. Without this the day's budget goes in the first busy hour and the
    # account is dark for the other 23 — the cap protects the balance, this
    # protects the coverage.
    hour_left = config.MAX_CLAUDE_ITEMS_PER_HOUR - state.claude_calls_this_hour()
    if hour_left <= 0:
        print(f"  hourly pace reached ({config.MAX_CLAUDE_ITEMS_PER_HOUR} items); "
              f"{len(items)} item(s) held for the next hour, not dropped")
        return []
    n = min(len(items), spendable, hour_left)
    if n < len(items):
        print(f"  budget allows {n} of {len(items)} item(s) this cycle; "
              f"the rest stay unseen and come back later")
    return items[:n]


def process_item(item: sources.NewsItem, result: dict | None) -> bool:
    """Decide whether `result` (Claude's verdict on `item`) should post, and post it.

    Returns True only if a tweet actually went out. The item is already marked
    seen and already past every unpaid gate — run_cycle applies those BEFORE
    composing, so nothing that cannot post reaches a paid call.
    """
    # A cap of 0 means uncapped — see config.MAX_POSTS_PER_DAY. Re-checked here
    # as well as pre-compose: posts land during this loop, so the cap can be
    # reached partway through a batch that is already paid for.
    if config.MAX_POSTS_PER_DAY and state.posts_today() >= config.MAX_POSTS_PER_DAY:
        print("Daily post cap reached; skipping remaining items")
        return False

    sig = sources.content_key(item.title)

    # These three used to share one "skipped:" line, which is why a day of zero
    # posts was indistinguishable from a day of API failures — 184 paid items
    # produced one identical message whether Claude had judged them or never
    # answered at all. Name which it was; the money is spent either way, but
    # only one of them is a bug.
    if result is None:
        print(f"  COMPOSE FAILED (paid, no verdict): {item.title[:66]}")
        return False
    if not result.get("newsworthy"):
        print(f"  not newsworthy [{result.get('category') or '?'}]: {item.title[:60]}")
        return False
    if not result.get("tweet"):
        print(f"  newsworthy but EMPTY TWEET (bug): {item.title[:60]}")
        return False

    # Semantic dedup: block a repeat of the SAME event even when the headline is
    # worded differently by another outlet. content_key above only catches near-
    # identical titles; this catches "Lu Dort traded to Hawks" vs "Thunder send
    # Dort to Atlanta" — same player, same destination -> posted once. Finals
    # dedup on the matchup, so each game's score posts exactly once.
    is_final = result.get("category") == "final"
    event_sig = _final_signature(result) if is_final else _event_signature(result)
    if event_sig and state.is_seen(event_sig):
        print(f"  duplicate event, skipping: {event_sig}")
        return False

    # BACKSTOP — deliberately not clever, and independent of how the model
    # classified the item. Every semantic dedup above depends on the model
    # filling the right fields; when it doesn't (a signing arriving as "chooses
    # Lakers in free agency" with is_trade unset), the feed fills with one story.
    # No single player is worth more than MAX_POSTS_PER_PLAYER items a day, so
    # the worst a future classification gap can cost is that, not a timeline.
    subject = _subject_key(result, f"{item.title} {item.summary}")
    if subject and state.player_posts_today(subject) >= config.MAX_POSTS_PER_PLAYER:
        print(f"  already posted {config.MAX_POSTS_PER_PLAYER} items about "
              f"{subject} today, skipping")
        return False

    # CONFIRMED-TRADE dedup: post a deal ONCE and never let it resurface. A
    # 3-team blockbuster (Dort + Risacher + picks) surfaces for DAYS from every
    # outlet naming a different player — collapse on the set of teams involved,
    # and the flags persist for days (not until midnight) so the same deal can't
    # come back a day later. Rumors/reports are deliberately NOT capped here.
    # A free-agent signing touches ONE team, so the ">=2 shared teams" rule below
    # can never fire for it — the player flag is the only thing standing between
    # you and four posts about the same signing. Gate on _is_player_move, not on
    # the model's is_trade alone.
    trade_teams, trade_pflag = set(), None
    if _is_player_move(result):
        trade_teams = _trade_team_set(result, f"{item.title} {item.summary}")
        trade_pflag = _trade_player_flag(result)
        if _trade_already_posted(trade_teams, trade_pflag):
            print(f"  trade already posted ({','.join(sorted(trade_teams)) or trade_pflag}), skipping")
            return False

    if is_final and not event_sig:
        print(f"  final with unresolvable teams, skipping: {item.title[:60]}")
        return False
    if is_final:
        a_s, h_s = int(result.get("away_score") or 0), int(result.get("home_score") or 0)
        # even summer-league teams clear 50; equal or tiny scores mean the model
        # couldn't actually read a final score out of the item
        if a_s < 50 or h_s < 50 or a_s == h_s:
            print(f"  final with implausible score ({a_s}-{h_s}), skipping")
            return False

    # Highlights (standout performances) only post for genuine stars, and are
    # capped separately per day so they add engagement without burying the news.
    is_highlight = result.get("category") == "highlight" or result.get("is_highlight")
    if is_highlight:
        if not result.get("is_star"):
            print(f"  non-star highlight, skipping: {item.title[:60]}")
            return False
        if state.highlights_today() >= config.MAX_HIGHLIGHTS_PER_DAY:
            print("  daily highlight cap reached; skipping highlight")
            return False

    # Freshness by type: transactions AND the trade/free-agency chatter around
    # them (rumors, reports — "star deciding today", "weighing offers") stay
    # worth posting for hours, so they get the wide window. Time-sensitive stuff
    # (a performance, a score, a quote) is stale within the tight window.
    if item.published_ts:
        age_min = (time.time() - item.published_ts) / 60
        # "official" belongs with them. It was on the TIGHT window, which meant
        # a confirmed announcement — a coach fired, a signing the team posted,
        # a suspension — was dropped 45 minutes after it broke, and those are
        # exactly the stories that surface slowly as each outlet writes them up.
        # A trade rumour got six hours while the club's own confirmation got
        # three quarters of one. Only a performance or a final score actually
        # goes cold that fast, so those keep the tight window.
        wide = result.get("is_trade") or result.get("category") in (
            "rumor", "report", "official")
        max_age = config.TRADE_MAX_AGE_MIN if wide else config.FRESH_MAX_AGE_MIN
        if age_min > max_age:
            print(f"  too stale ({int(age_min)}m old), skipping: {item.title[:60]}")
            return False

    text = result["tweet"].strip()

    # A report or rumour must name who reported it. SYSTEM_PROMPT has said so
    # from the start — "Rumors and reports MUST name the source in the tweet" —
    # but nothing enforced it, and on 2026-07-23 two five-month-old trades went
    # out seven seconds apart as bare assertions: "Pelicans trade Jose Alvarado
    # to the Knicks for Dalen Terry and two second-round picks." Both were
    # category=report, both carried the 🚨 official prefix they were not
    # entitled to, and neither said who was reporting it.
    #
    # That combination is the tell. A model that has dropped the attribution has
    # stopped describing what an ARTICLE SAYS and started asserting the event
    # itself as fact — which is exactly the voice a stale recap gets rewritten
    # into. We cannot verify the date (Google News hides the publisher URL
    # behind an opaque redirect, so there is no article date to read), but we
    # can refuse to publish an unsourced claim, and that catches this case.
    if result.get("category") in ("rumor", "report"):
        if not _names_a_source(text):
            print(f"  {result.get('category')} with no source named, skipping: "
                  f"{text[:88]}")
            return False

    if item.link and config.INCLUDE_SOURCE_LINK:
        text = f"{text}\n{item.link}"
    else:
        text = _delink(text)

    # Final duplicate check, on the finished tweet rather than on anything that
    # produced it. Runs AFTER compose because that is the whole idea — the model
    # has normalised two differently-worded stories into one voice by here, so
    # near-copies look alike even when no upstream key matched. Costs the Claude
    # call, saves the duplicate post; the post is the part followers see.
    twin = _too_similar_to_recent(text)
    if twin:
        print(f"  near-duplicate of a recent post, skipping:\n"
              f"    new:  {text[:88]}\n"
              f"    prev: {twin[:88]}")
        return False

    # Auto-generate a graphic: a FINAL score card for game results (attaching
    # our own media also stops X from showing the linked article's ugly
    # auto-scoreboard preview), or a trade card for player moves.
    image = None
    if is_final:
        # Real player from the game as a blurred backdrop when we can find a
        # free-licensed photo of the named standout; else the procedural arena.
        photo, credit = None, None
        star = result.get("star_player")
        if star:
            res = photos.get_player_photo(star)
            if res:
                photo, credit = res
        image = card.make_score_card(
            away_team=result.get("away_team") or "",
            home_team=result.get("home_team") or "",
            away_score=int(result.get("away_score") or 0),
            home_score=int(result.get("home_score") or 0),
            source=item.source,
            photo=photo,
            credit=credit,
        )
        if image:
            bg = f"photo:{star}" if photo else "arena"
            print(f"  generated score card ({bg}): {result.get('away_team')} {result.get('away_score')}"
                  f" @ {result.get('home_team')} {result.get('home_score')}")
    # Gate the card on _is_player_move, NOT on is_trade — the same reason the
    # dedup had to stop trusting it. A signing arrives as "Lakers sign Arthur
    # Kaluma to a two-way contract" with is_trade unset but player and to_team
    # both filled, which cleared every dedup check and then silently failed
    # this one, so the post went out bare. Signings are most of the summer
    # feed, so most of the timeline lost its card. Still requires player and
    # to_team, because make_trade_card cannot draw a move without both ends.
    elif _is_player_move(result) and result.get("player") and result.get("to_team"):
        photo, credit = None, None
        # Every traded/signed player gets a photo: a free Wikimedia action shot
        # when one exists, else the player's official headshot. Only a total
        # miss (name unresolvable at ESPN too) falls to the logo design card.
        res = photos.get_any_photo(result["player"])
        if res:
            photo, credit = res
        image = card.make_trade_card(
            player=result["player"],
            to_team=result["to_team"],
            from_team=result.get("from_team") or None,
            source=item.source,
            photo=photo,
            credit=credit,
        )
        if image:
            kind = "photo" if photo else "design"
            print(f"  generated {kind} trade card: {result['player']} -> {result['to_team']}")

    # Last resort before the REQUIRE_IMAGE gate below: a generic news card.
    #
    # Only finals and player moves had a generator, so an investigation, a
    # coaching hire, a suspension or an injury reached this point with no image
    # and was dropped — after being fetched, filtered and PAID for. Three real
    # stories died this way overnight on 2026-07-21. Buying the news and then
    # binning it for want of a picture is the worst of both.
    if image is None:
        try:
            # Pull a player photo when the story names one. Without this the
            # news card was logo-only while every trade and score card showed a
            # player — the same news looked cheaper purely because of which
            # generator happened to handle it.
            nphoto, ncredit = None, None
            story_teams = sorted(_trade_team_set(
                result, f"{item.title} {item.summary}"))
            # `player` now carries coaches and executives too, so a firing gets
            # a face instead of a bare logo — Commons has coaches even though
            # ESPN's athlete search doesn't. The teams are passed so a
            # former-team jersey can be ranked down.
            if result.get("player"):
                res = photos.get_any_photo(result["player"], teams=story_teams)
                if res:
                    nphoto, ncredit = res
            image = card.make_news_card(
                # The tweet is now only the FALLBACK. The card shows the short
                # label the composer produced, so the detail lives in the post
                # and the graphic carries one glanceable fact.
                headline=result["tweet"].strip(),
                label=card.build_label(result),
                photo=nphoto, credit=ncredit,
                # Whatever teams the story touches, for colour and logos. Empty
                # is fine — the card falls back to a neutral league wash rather
                # than returning None, which is what was losing the post.
                teams=story_teams,
                # Whoever the tweet credits, not whichever feed carried it.
                source=_reported_by(text, item.source),
            )
            if image:
                print(f"  generated news card (category={result.get('category') or '?'})")
        except Exception as e:
            print(f"  news card failed (continuing): {e}")

    # One image per post, or it doesn't go out (config.REQUIRE_IMAGE). Checked
    # here rather than earlier so the decision is made on the card we actually
    # produced — a generator that returns None (missing logo, unresolvable
    # player, CDN hiccup) drops the post exactly like a category with no
    # generator at all, instead of quietly posting bare.
    if image is None and config.REQUIRE_IMAGE:
        print(f"  no card for this item (category={result.get('category') or '?'}), "
              f"skipping — REQUIRE_IMAGE is on")
        return False

    if tweeter.post(text, image=image):
        # What went out, and what it came from. Every SKIP was logged and every
        # POST was silent, so the one event worth auditing left no trace: when
        # two stale trades went out on 2026-07-23 the logs could not say which
        # feed carried them, how old the item claimed to be, or what the source
        # headline was. Three runs were searched for "Alvarado" and matched
        # nothing, because a successful post printed no text at all.
        age = (f"{int((time.time() - item.published_ts) / 60)}m"
               if item.published_ts else "undated")
        print(f"  POSTED [{result.get('category') or '?'}] age={age} "
              f"feed={item.source} :: {text[:100]}\n"
              f"    from: {item.title[:110]}\n"
              f"    link: {item.link[:110]}")
        state.incr_posts()
        state.record_post(result["tweet"].strip(), result.get("category", ""), bool(image))
        if is_highlight:
            state.incr_highlights()
        if sig:
            state.mark_seen(f"sig:{sig}")  # block dupes of this story going forward
        if event_sig:
            state.mark_seen(event_sig)  # highlight: one per player per day
        if subject:
            state.incr_player_posts(subject)
        # Remember the raw name too, so _covered_subject_in can scan tomorrow's
        # headlines for it without paying Claude to identify the subject first.
        if result.get("player"):
            state.record_posted_name(result["player"])
        if _is_player_move(result):
            # persistent flags: this move never resurfaces (any outlet/wording/day)
            _mark_trade_posted(trade_teams, trade_pflag)
        return True
    return False


def run_cycle(include_rss: bool = True) -> None:
    """One pass. `include_rss=False` polls only the insider X accounts.

    The two sources are polled at different rates because they are limited by
    different things. Google News throttles on REQUEST RATE — 60s polling got
    the runner's IP blocked for four hours on 2026-07-21 — so RSS wants a slow,
    polite cadence. The X reader is bounded by tweets RETURNED, and `since_id`
    means an idle poll returns none and bills $0.005 x 0. Polling it three times
    as often is therefore free, and it is the source that breaks news first.
    """
    # Evergreen debate post (once/day, evening ET) — runs even when there's no
    # news, which is the whole point: it fills the quiet stretches.
    if include_rss:
        try:
            maybe_post_engagement()
        except Exception as e:
            print(f"engagement post error (continuing): {e}")

    # Pull candidates up to the widest window (trades stay newsworthy for hours);
    # the tighter per-type freshness is enforced in process_item once Claude has
    # told us whether the item is actually a trade.
    candidate_age = max(config.FRESH_MAX_AGE_MIN, config.TRADE_MAX_AGE_MIN) * 60
    # is_fresh (local, free) is checked BEFORE is_seen (a Redis read), so we only
    # hit Redis for items new enough to matter — far fewer Redis commands.
    all_items = []
    # Insider tweets join the same funnel as RSS. They arrive first — a scoop
    # read from the tweet beats the same scoop read from an article about the
    # tweet — and _insider_first() below keeps them at the front of the queue.
    try:
        all_items = insiders.fetch_insider_items()
    except Exception as e:
        print(f"insider X read error (continuing): {e}")
    if include_rss:
        all_items = all_items + sources.fetch_all()
        # Snapshot which feeds are live/quiet/down for the dashboard's "sources"
        # view. Only on RSS cycles — LAST_HEALTH is untouched on an
        # insider-only pass, and writing it anyway would republish a stale
        # snapshot as if it were fresh.
        try:
            state.set_feed_health(sources.LAST_HEALTH)
        except Exception as e:
            print(f"feed-health snapshot error (continuing): {e}")
    fresh = [i for i in all_items
             if sources.is_fresh(i, candidate_age) and not state.is_seen(i.id)]

    # Collapse the same story arriving from multiple feeds into ONE item BEFORE
    # any Claude call — Google-News queries overlap heavily, and each duplicate
    # was its own paid request.
    seen_keys, deduped = set(), []
    for i in fresh:
        k = sources.content_key(i.title)
        if k and k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(i)

    # Free prefilter: junk never reaches Claude.
    worth = [i for i in deduped if _worth_composing(i)]
    # Scoops first, so a tight hourly budget buys the insider break rather than
    # whichever aggregator reprinted it a minute later.
    worth = _insider_first(worth)
    if config.SKIP_COVERED_SUBJECTS:
        before = len(worth)
        worth = _collapse_same_story(worth)
        if before != len(worth):
            print(f"  collapsed {before - len(worth)} same-story item(s) before composing")
    if not fresh:
        return

    # Last unpaid gates, hoisted ahead of composing now that composing is
    # batched. Everything here is free or a Redis read, and every item it drops
    # was going to be rejected after the call anyway — so paying for it first
    # bought nothing.
    pending = []
    for item in worth:
        if config.MAX_POSTS_PER_DAY and state.posts_today() >= config.MAX_POSTS_PER_DAY:
            print("Daily post cap reached; composing nothing further this cycle")
            break
        sig = sources.content_key(item.title)
        if sig and state.is_seen(f"sig:{sig}"):
            state.mark_seen(item.id)
            print(f"  duplicate story, skipping: {item.title[:60]}")
            continue
        if config.SKIP_COVERED_SUBJECTS:
            covered = _covered_subject_in(item.title)
            if covered:
                state.mark_seen(item.id)
                print(f"  already covered {covered} today, skipping before the Claude "
                      f"call: {item.title[:60]}")
                continue
        pending.append(item)

    # Trim to what the budget allows. Held-back items are deliberately NOT
    # marked seen, so they return next hour instead of being dropped — the old
    # code marked every item seen on entry and only then checked the budget, so
    # a story that broke while the hourly allowance was spent was gone for good.
    affordable = _affordable(pending)
    for item in affordable:
        state.mark_seen(item.id)

    print(f"{len(fresh)} fresh | -{len(fresh) - len(deduped)} cross-feed dupes | "
          f"-{len(deduped) - len(worth)} prefiltered junk | "
          f"-{len(worth) - len(pending)} already covered | "
          f"{len(affordable)} -> Claude"
          + (f" (+{len(pending) - len(affordable)} held for budget)"
             if len(affordable) < len(pending) else ""))

    # One request per CLAUDE_BATCH_SIZE items. Post each batch's verdicts as
    # they land rather than composing everything first, so the freshest story
    # goes out sooner on a busy cycle.
    size = max(1, config.CLAUDE_BATCH_SIZE)
    for start in range(0, len(affordable), size):
        chunk = affordable[start:start + size]
        results = composer.compose_batch(chunk)
        # Charge what it actually cost, not how many headlines went in: an item
        # the batch could not answer for was retried individually at ~4.3x the
        # batched price, and the ceiling is a DOLLAR ceiling wearing an item
        # count as a costume. Without this a systematic batch failure spends 4x
        # the day's budget with the counter reading normal.
        fell_back = composer.LAST_FALLBACKS[0]
        charge = len(chunk) + fell_back * (composer.FALLBACK_COST_WEIGHT - 1)
        if fell_back:
            print(f"  charging {charge} budget units for {len(chunk)} item(s) "
                  f"({fell_back} un-batched at {composer.FALLBACK_COST_WEIGHT}x)")
        state.incr_claude_calls(charge)
        state.incr_claude_calls_hour(charge)
        for item, result in zip(chunk, results):
            # Pace only ACTUAL posts. This used to sleep after every item,
            # skipped ones included — 60 candidates meant two minutes of
            # sleeping to space out the handful that published.
            if process_item(item, result):
                time.sleep(2)  # small gap between posts, looks less bot-bursty

    if config.INSIDER_X_ENABLED:
        print(f"  {insiders.usage_line()}")
    u = composer.USAGE
    print(f"Claude usage this cycle: {u['calls']} calls, {u['input']} in / "
          f"{u['output']} out tokens (cache: {u['cache_read']} read, "
          f"{u['cache_creation']} created)")


def baseline() -> None:
    """Mark everything currently in the feeds as seen so the bot doesn't flood
    the timeline with backlog on its very first run."""
    sample = sources.fetch_all()
    for i in sample:
        state.mark_seen(i.id)
    print(f"First run: baselined {len(sample)} existing items. "
          "Only news from now on will be tweeted.\n")


def main() -> None:
    once = "--once" in sys.argv
    mode = "DRY RUN (printing only)" if config.DRY_RUN else "LIVE (posting to X)"
    print(f"NBA news bot — {mode} — state: {state.backend()}")
    if not config.DRY_RUN:
        print(tweeter.creds_report())

    # First run ever: baseline instead of posting the whole backlog
    if not state.has_any_seen():
        baseline()
        if once:
            return

    if once:
        # Single pass for scheduled/cron invocation
        run_cycle()
        return

    cap = f"max {config.MAX_POSTS_PER_DAY} posts/day" if config.MAX_POSTS_PER_DAY else "no daily post cap"
    tick = max(5, min(config.INSIDER_POLL_SECONDS, config.POLL_SECONDS))
    print(f"Polling {len(config.FEEDS)} feeds every {config.POLL_SECONDS}s, "
          f"{len(config.INSIDER_X_ACCOUNTS)} X account(s) every {tick}s, {cap}\n")
    # The loop ticks at the FASTER rate and gates the slow source on elapsed
    # time, rather than running two threads. One thread means no lock around
    # the Redis counters or the per-cycle Claude budget, and the whole job is
    # network wait anyway.
    last_rss = 0.0
    while True:
        try:
            due = (time.time() - last_rss) >= config.POLL_SECONDS
            if due:
                last_rss = time.time()
            run_cycle(include_rss=due)
        except KeyboardInterrupt:
            print("\nStopping.")
            break
        except Exception as e:
            print(f"Cycle error (continuing): {e}")
        time.sleep(tick)


if __name__ == "__main__":
    main()
