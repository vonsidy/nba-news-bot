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
import config
import photos
import sources
import state
import tweeter
from composer import compose


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
    """A signature for the underlying *event*, independent of how any outlet
    worded the headline, which name form it used, or which destination it named.
    A player's trade posts AT MOST ONCE PER DAY — every wording ("Lu Dort to
    Hawks", "Thunder send Luguentz Dort to Atlanta", a three-team-deal writeup)
    collapses to one key, so the same player never spams the timeline. A
    DIFFERENT player from the same multi-player trade has his own key and still
    posts. Highlights are likewise one-per-player-per-day. No dependency on
    resolving the team, so an unrecognized destination can't slip a dupe through."""
    player = _player_key(result.get("player"))
    if not player:
        return None
    if result.get("is_trade"):
        return f"trade:{player}:{state.today_key()}"
    if result.get("category") == "highlight" or result.get("is_highlight"):
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


def _trade_already_posted(teams: set) -> bool:
    """True if a trade sharing >=2 teams with `teams` already went out today —
    i.e. it's the same deal, just a different player or a different outlet's
    wording. Two teams of overlap is the reliable signal: distinct trades rarely
    involve the same pair on the same day."""
    day = state.today_key()
    return sum(1 for t in teams if state.is_seen(f"tt:{t}:{day}")) >= 2

# Windows consoles default to cp1252, which crashes on emoji in headlines/tweets
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def process_item(item: sources.NewsItem) -> None:
    # Mark seen first — a bad item shouldn't be retried forever
    state.mark_seen(item.id)

    if state.posts_today() >= config.MAX_POSTS_PER_DAY:
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

    # Trade-level dedup: post a deal ONCE, not once per player it moves. A
    # 3-team blockbuster (Dort + Risacher + picks) surfaces for days from every
    # outlet naming a different player — the per-player key above lets each
    # through, so collapse on the set of teams involved instead.
    trade_teams = (_trade_team_set(result, f"{item.title} {item.summary}")
                   if result.get("is_trade") else set())
    if trade_teams and _trade_already_posted(trade_teams):
        print(f"  same trade already posted today ({','.join(sorted(trade_teams))}), skipping")
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
    if item.link:
        text = f"{text}\n{item.link}"

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
    elif result.get("is_trade") and result.get("player") and result.get("to_team"):
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
        if is_highlight:
            state.incr_highlights()
        if sig:
            state.mark_seen(f"sig:{sig}")  # block dupes of this story going forward
        if event_sig:
            state.mark_seen(event_sig)  # block dupes of this event (any wording)
        for t in trade_teams:  # block the rest of this multi-player deal today
            state.mark_seen(f"tt:{t}:{state.today_key()}")


def run_cycle() -> None:
    # Pull candidates up to the widest window (trades stay newsworthy for hours);
    # the tighter per-type freshness is enforced in process_item once Claude has
    # told us whether the item is actually a trade.
    candidate_age = max(config.FRESH_MAX_AGE_MIN, config.TRADE_MAX_AGE_MIN) * 60
    items = [
        i for i in sources.fetch_all()
        if not state.is_seen(i.id) and sources.is_fresh(i, candidate_age)
    ]
    if not items:
        return
    print(f"{len(items)} new item(s)")
    for item in items:
        process_item(item)
        time.sleep(2)  # small gap between posts, looks less bot-bursty


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

    print(f"Polling {len(config.FEEDS)} feeds every {config.POLL_SECONDS}s, "
          f"max {config.MAX_POSTS_PER_DAY} posts/day\n")
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
