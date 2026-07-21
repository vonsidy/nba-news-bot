"""Regression test for the story-duplication bugs.

Run: python tools/test_dedup.py   (no network, no API keys, no Redis)

On 2026-07-20 the bot posted four items about Matisse Thybulle signing with the
Lakers, from four outlets, inside 24 minutes. Two independent bugs:

  1. The persistent flag was keyed player+destination. Outlets fill to_team
     inconsistently for one event ("LAL" / "Lakers" / empty for a free agent who
     "chooses" a team), and an empty to_team produced a different key entirely.
  2. The dedup was gated on the model's is_trade, which comes back unset for
     free-agency phrasing — and the ">=2 shared teams" rule can never fire for a
     signing, which touches one team.

This replays those four posts through the real logic. It also asserts the
per-player backstop bounds the damage even if the semantic dedup misses.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Stub the heavy imports so the pure dedup logic can run anywhere.
for name in ("anthropic", "tweepy", "feedparser", "dotenv"):
    m = types.ModuleType(name)
    if name == "dotenv":
        m.load_dotenv = lambda *a, **k: None
    if name == "anthropic":
        m.Anthropic = lambda *a, **k: types.SimpleNamespace()
    if name == "tweepy":
        m.TweepyException = Exception
        m.TooManyRequests = Exception
        m.Client = m.API = m.OAuth1UserHandler = lambda *a, **k: types.SimpleNamespace()
    if name == "feedparser":
        m.parse = lambda *a, **k: types.SimpleNamespace(entries=[])
    sys.modules.setdefault(name, m)
_pil = types.ModuleType("PIL")
for _sub in ("Image", "ImageDraw", "ImageFont", "ImageFilter"):
    setattr(_pil, _sub, types.SimpleNamespace())
    sys.modules.setdefault(f"PIL.{_sub}", types.ModuleType(f"PIL.{_sub}"))
sys.modules.setdefault("PIL", _pil)

import bot      # noqa: E402
import config   # noqa: E402
import state    # noqa: E402

# The four posts that actually went out, oldest first, with the structured
# fields the composer plausibly returned for each phrasing.
POSTS = [
    ("Sportsnet 'Lakers sign Matisse Thybulle to one-year deal'",
     {"player": "Matisse Thybulle", "to_team": "LAL", "from_team": "", "is_trade": True}),
    ("HoopsRumors 'Lakers sign Matisse Thybulle to one-year, $3.3MM contract'",
     {"player": "Matisse Thybulle", "to_team": "Lakers", "from_team": "", "is_trade": True}),
    ("Shams 'Matisse Thybulle chooses Lakers in free agency'",
     {"player": "Matisse Thybulle", "to_team": "Lakers", "from_team": "", "is_trade": False}),
    ("mcten 'Lakers add Thybulle, reach 16 guaranteed contracts'",
     {"player": "Matisse Thybulle", "to_team": "LAL", "from_team": "", "is_trade": False}),
]


def test_semantic_dedup():
    flags = {}
    state.get_flag = lambda k: k in flags
    state.set_flag = lambda k, ttl: flags.__setitem__(k, ttl)

    posted = []
    for label, result in POSTS:
        if not bot._is_player_move(result):
            posted.append(label + "  [NOT DETECTED AS A MOVE]")
            continue
        teams = bot._trade_team_set(result, label)
        pflag = bot._trade_player_flag(result)
        if bot._trade_already_posted(teams, pflag):
            continue
        posted.append(label)
        bot._mark_trade_posted(teams, pflag)

    assert len(posted) == 1, f"expected 1 post, got {len(posted)}: {posted}"

    # A different player signing with the same team must still post.
    other = {"player": "Ziaire Williams", "to_team": "LAL", "from_team": "", "is_trade": True}
    assert not bot._trade_already_posted(
        bot._trade_team_set(other, "Lakers sign Ziaire Williams"),
        bot._trade_player_flag(other),
    ), "a different player's signing must not be suppressed"
    print(f"semantic dedup: 1 posted, {len(POSTS) - 1} skipped  OK")


def test_backstop_bounds_damage():
    """Even assuming the semantic dedup misses entirely, one player cannot
    dominate the feed."""
    counts = {}
    state.player_posts_today = lambda p: counts.get(p, 0)
    state.incr_player_posts = lambda p: counts.__setitem__(p, counts.get(p, 0) + 1)

    survived = 0
    for _label, result in POSTS:
        subject = bot._player_key(result.get("player"))
        if subject and state.player_posts_today(subject) >= config.MAX_POSTS_PER_PLAYER:
            continue
        survived += 1
        state.incr_player_posts(subject)

    assert survived == config.MAX_POSTS_PER_PLAYER, (
        f"backstop let {survived} through, cap is {config.MAX_POSTS_PER_PLAYER}")
    assert state.player_posts_today(bot._player_key("Ziaire Williams")) == 0, \
        "the backstop must be per-player, not global"
    print(f"backstop: bounded to {survived} per player  OK")


def test_non_player_news_is_bounded():
    """News with no player in it — coaching hires, front-office moves — has no
    player key to dedup on. Without a team fallback it was completely
    unprotected, which is the same failure shape as the Thybulle signing."""
    counts = {}
    state.player_posts_today = lambda p: counts.get(p, 0)
    state.incr_player_posts = lambda p: counts.__setitem__(p, counts.get(p, 0) + 1)

    coach_story = {"player": "", "to_team": "", "from_team": "", "is_trade": False}
    headlines = [
        "Lakers hire Mike Brown as head coach",
        "Mike Brown named Lakers head coach, per sources",
        "Lakers finalize deal with Mike Brown to lead the bench",
        "Report: Lakers land Mike Brown as next head coach",
    ]

    survived = 0
    for headline in headlines:
        subject = bot._subject_key(coach_story, headline)
        assert subject, f"no subject derived for: {headline}"
        if state.player_posts_today(subject) >= config.MAX_POSTS_PER_PLAYER:
            continue
        survived += 1
        state.incr_player_posts(subject)

    assert survived == config.MAX_POSTS_PER_PLAYER, (
        f"coaching news let {survived} through, cap is {config.MAX_POSTS_PER_PLAYER}")

    # A different team's coaching news is unaffected.
    other = bot._subject_key(coach_story, "Suns hire a new head coach")
    assert state.player_posts_today(other) == 0, "team backstop must be per-team"

    # Player and team keys must not share an allowance.
    player_subj = bot._subject_key({"player": "Matisse Thybulle"}, "Lakers sign Thybulle")
    team_subj = bot._subject_key(coach_story, "Lakers hire Mike Brown")
    assert player_subj != team_subj, "player and team keys must be distinct"
    print(f"non-player news: bounded to {survived} per team  OK")


def test_traded_player_posts_once():
    """The trade half of the same guarantee. POSTS above is a free-agent
    signing; a trade differs in that from_team is populated and outlets
    disagree about BOTH endpoints — abbreviation, nickname, full name, or
    missing entirely — and some file it as a rumor with is_trade unset. Keyed
    on the player alone, every one of those collapses to a single post."""
    flags = {}
    state.get_flag = lambda k: k in flags
    state.set_flag = lambda k, ttl: flags.__setitem__(k, ttl)

    trade = [
        ("Woj 'Nets trade Cam Johnson to Nuggets'",
         {"player": "Cam Johnson", "from_team": "BKN", "to_team": "DEN", "is_trade": True}),
        ("ESPN 'Nuggets acquire Cameron Johnson from Brooklyn'",
         {"player": "Cameron Johnson", "from_team": "Brooklyn", "to_team": "Nuggets", "is_trade": True}),
        ("HoopsHype 'Cam Johnson on the move to Denver'",
         {"player": "Cam Johnson", "from_team": "", "to_team": "Denver", "is_trade": False}),
        ("RealGM 'Johnson dealt in Nets-Nuggets swap'",
         {"player": "Cam Johnson", "from_team": "Nets", "to_team": "", "is_trade": False}),
    ]

    posted = []
    for label, result in trade:
        if not bot._is_player_move(result):
            posted.append(label + "  [NOT DETECTED AS A MOVE]")
            continue
        teams = bot._trade_team_set(result, label)
        pflag = bot._trade_player_flag(result)
        if bot._trade_already_posted(teams, pflag):
            continue
        posted.append(label)
        bot._mark_trade_posted(teams, pflag)

    assert len(posted) == 1, f"expected 1 post for one trade, got {len(posted)}: {posted}"

    # The flag is persistent, so the same trade resurfacing tomorrow stays dead.
    next_day = {"player": "Cam Johnson", "from_team": "BKN", "to_team": "DEN", "is_trade": True}
    assert bot._trade_already_posted(
        bot._trade_team_set(next_day, "Nets-Nuggets deal official"),
        bot._trade_player_flag(next_day),
    ), "a posted trade must not resurface on a later day"

    # The other player in the same deal is a separate subject and may post.
    counterpart = {"player": "Michael Porter Jr.", "from_team": "DEN", "to_team": "BKN", "is_trade": True}
    assert not bot._trade_already_posted(
        set(), bot._trade_player_flag(counterpart)
    ), "a different player must not be suppressed by another player's flag"
    print("trade: one player, four phrasings -> 1 post, no next-day resurface  OK")


if __name__ == "__main__":
    test_semantic_dedup()
    test_traded_player_posts_once()
    test_backstop_bounds_damage()
    test_non_player_news_is_bounded()
    print("\nPASS")


# ---------------------------------------------------------------------------
# Output-side backstop: catches duplicates no structural key matched.
# ---------------------------------------------------------------------------
def test_near_duplicate_tweet_blocked():
    import state
    real = state.recent_posts
    try:
        # The exact pair that reached the timeline on 2026-07-21.
        posted = ("🚨 OFFICIAL: Jamarion Sharp signs two-way deal with the Clippers. "
                  "The G League Defensive Player of the Year went undrafted out of Ole Miss in 2024.")
        state.recent_posts = lambda n=20: [{"text": posted}]

        dupe = "📰 REPORT: Summer leaguer Sharp signs two-way deal with Clippers via Sportsnet"
        assert bot._too_similar_to_recent(dupe), "the duplicate that shipped is still allowed"

        # A DIFFERENT signing on the same day must still go out.
        other = "🚨 OFFICIAL: Jett Howard signs a two-way contract with the Mavericks."
        assert not bot._too_similar_to_recent(other), \
            "blocked a genuinely different signing — too aggressive"

        # Different player, same team, same day.
        same_team = ("🚨 OFFICIAL: Kobe Brown signs a two-way deal with the Clippers. "
                     "The 2023 first-round pick returns on a new contract.")
        assert not bot._too_similar_to_recent(same_team), \
            "blocked a different player on the same team"

        # Same story reworded by another outlet — the case no upstream key catches.
        reworded = "📰 REPORT: Clippers sign Jamarion Sharp to a two-way deal, per sources."
        assert bot._too_similar_to_recent(reworded), "reworded duplicate allowed"

        # Unrelated news about the same team must still post.
        other_team_news = "🚨 OFFICIAL: Clippers waive guard Bones Hyland after three seasons."
        assert not bot._too_similar_to_recent(other_team_news), \
            "blocked unrelated news about the same team"

        # Trivially short text must not be judged at all.
        assert not bot._too_similar_to_recent("🔥 40 points")
    finally:
        state.recent_posts = real
    print("near-duplicate tweet blocked, distinct signings still post  OK")


test_near_duplicate_tweet_blocked()
print("\nPASS")
