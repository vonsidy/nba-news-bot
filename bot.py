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
import photos
import sources
import state
import tweeter
from composer import compose


# Anything X would turn into a t.co link: an explicit scheme, or a bare domain
# it linkifies on sight. Matching this in a tweet body means paying $0.200 for
# that post instead of $0.015.
_LINKIFIED = re.compile(
    r"(?i)(https?://\S+|\b[a-z0-9][a-z0-9-]*\.(?:io|com|net|org|co|uk|tv|us|gg|app|news)\b)"
)
_BARE_DOMAIN = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9-]*)\.(?:io|com|net|org|co|uk|tv|us|gg|app|news)\b"
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
    r"|summer league (recap|grades|review|takeaways|standings))\b"
)
_OTHER_SPORT_RE = re.compile(
    r"(?i)\b(NFL|NHL|MLB|MLS|WNBA|cricket|rugby|premier league|la liga|bundesliga"
    r"|euroleague|serie a|formula 1|nascar|pga|ufc|maple leafs|canadiens"
    r"|\bjets\b|yankees|dodgers|red sox|packers|cowboys)\b"
)
# NBA signal = the word "NBA" or any team city/nickname (>=4 chars to avoid noise).
_NBA_TOKENS = sorted(
    {a for a in card._ALIASES if len(a) >= 4} | {t.lower() for t in card.TEAMS},
    key=len, reverse=True,
)
_NBA_RE = re.compile(r"(?i)\b(nba|" + "|".join(re.escape(x) for x in _NBA_TOKENS) + r")\b")


def _worth_composing(item: sources.NewsItem) -> bool:
    """True if the item is worth a paid Claude call. Drops clear junk (betting,
    fantasy, listicles, schedules) and other-sports items that carry no NBA
    signal. Everything else goes to Claude — when in doubt, let it through."""
    t = f"{item.title} {item.summary}"
    if _JUNK_RE.search(t):
        return False
    if _OTHER_SPORT_RE.search(t) and not _NBA_RE.search(t):
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
_TRADE_TTL = 6 * 24 * 3600


def _trade_player_flag(result: dict) -> str | None:
    """Persistent per-player-move key: this player moved, so post it once.

    Deliberately NOT keyed on the destination. Outlets fill to_team differently
    for the same event — "LAL" / "Lakers" / empty for a free agent who "chooses"
    a team — and a destination-keyed flag silently fails to collapse those: the
    `dest or 'x'` fallback used to produce a DIFFERENT key whenever to_team came
    back empty, so the same signing posted again. A player moves once; that's the
    whole key."""
    p = _player_key(result.get("player"))
    return f"moved:{p}" if p else None


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
    p = _player_key(result.get("player"))
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


def process_item(item: sources.NewsItem) -> None:
    # Mark seen first — a bad item shouldn't be retried forever
    state.mark_seen(item.id)

    # A cap of 0 means uncapped (the default) — see config.MAX_POSTS_PER_DAY.
    if config.MAX_POSTS_PER_DAY and state.posts_today() >= config.MAX_POSTS_PER_DAY:
        print("Daily post cap reached; skipping remaining items")
        return

    # Skip a story we've already posted from another outlet/feed. The Google
    # News feeds aggregate every publisher, so the same trade surfaces many
    # times with different links — dedup on the headline's meaning, and do it
    # before the Claude call so duplicates cost nothing.
    sig = sources.content_key(item.title)
    if sig and state.is_seen(f"sig:{sig}"):
        print(f"  duplicate story, skipping: {item.title[:60]}")
        return

    result = compose(item)
    if not result or not result.get("newsworthy") or not result.get("tweet"):
        print(f"  skipped: {item.title[:70]}")
        return

    # Semantic dedup: block a repeat of the SAME event even when the headline is
    # worded differently by another outlet. content_key above only catches near-
    # identical titles; this catches "Lu Dort traded to Hawks" vs "Thunder send
    # Dort to Atlanta" — same player, same destination -> posted once. Finals
    # dedup on the matchup, so each game's score posts exactly once.
    is_final = result.get("category") == "final"
    event_sig = _final_signature(result) if is_final else _event_signature(result)
    if event_sig and state.is_seen(event_sig):
        print(f"  duplicate event, skipping: {event_sig}")
        return

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
        return

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
            return

    if is_final and not event_sig:
        print(f"  final with unresolvable teams, skipping: {item.title[:60]}")
        return
    if is_final:
        a_s, h_s = int(result.get("away_score") or 0), int(result.get("home_score") or 0)
        # even summer-league teams clear 50; equal or tiny scores mean the model
        # couldn't actually read a final score out of the item
        if a_s < 50 or h_s < 50 or a_s == h_s:
            print(f"  final with implausible score ({a_s}-{h_s}), skipping")
            return

    # Highlights (standout performances) only post for genuine stars, and are
    # capped separately per day so they add engagement without burying the news.
    is_highlight = result.get("category") == "highlight" or result.get("is_highlight")
    if is_highlight:
        if not result.get("is_star"):
            print(f"  non-star highlight, skipping: {item.title[:60]}")
            return
        if state.highlights_today() >= config.MAX_HIGHLIGHTS_PER_DAY:
            print("  daily highlight cap reached; skipping highlight")
            return

    # Freshness by type: transactions AND the trade/free-agency chatter around
    # them (rumors, reports — "star deciding today", "weighing offers") stay
    # worth posting for hours, so they get the wide window. Time-sensitive stuff
    # (a performance, a score, a quote) is stale within the tight window.
    if item.published_ts:
        age_min = (time.time() - item.published_ts) / 60
        wide = result.get("is_trade") or result.get("category") in ("rumor", "report")
        max_age = config.TRADE_MAX_AGE_MIN if wide else config.FRESH_MAX_AGE_MIN
        if age_min > max_age:
            print(f"  too stale ({int(age_min)}m old), skipping: {item.title[:60]}")
            return

    text = result["tweet"].strip()
    if item.link and config.INCLUDE_SOURCE_LINK:
        text = f"{text}\n{item.link}"
    else:
        text = _delink(text)

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

    if tweeter.post(text, image=image):
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
        if _is_player_move(result):
            # persistent flags: this move never resurfaces (any outlet/wording/day)
            _mark_trade_posted(trade_teams, trade_pflag)


def run_cycle() -> None:
    # Evergreen debate post (once/day, evening ET) — runs even when there's no
    # news, which is the whole point: it fills the quiet stretches.
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
    all_items = sources.fetch_all()
    # Snapshot which feeds are live/quiet/down for the dashboard's "sources" view.
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
    if not fresh:
        return
    print(f"{len(fresh)} fresh | -{len(fresh) - len(deduped)} cross-feed dupes | "
          f"-{len(deduped) - len(worth)} prefiltered junk | {len(worth)} -> Claude")
    for item in worth:
        process_item(item)
        time.sleep(2)  # small gap between posts, looks less bot-bursty

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
    print(f"Polling {len(config.FEEDS)} feeds every {config.POLL_SECONDS}s, {cap}\n")
    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            print("\nStopping.")
            break
        except Exception as e:
            print(f"Cycle error (continuing): {e}")
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()
