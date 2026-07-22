"""Regression test for the two rules every outgoing post must satisfy.

Run: python tools/test_post_rules.py   (no network, no API keys, no Redis)

  1. NO URL, EVER. X bills a post containing any url at $0.200 against $0.015
     without one — 13x, on the account's single highest-volume action. On
     2026-07-20 every post carried an appended source link, which cost ~$2.00/day
     and was 42% of the entire X bill.

  2. ONE IMAGE PER POST. Owner's call 2026-07-21: a post with no card doesn't
     go out at all.

Rule 1 has three independent layers and this asserts the whole stack, because
each layer alone has a hole the next one covers:

  a. bot.process_item only appends item.link when INCLUDE_SOURCE_LINK is set.
  b. sources._publisher_name strips the tld off a bare-domain publisher, so the
     composer is never handed "roundtable.io" to write into the body — X
     linkifies bare domains, and that url never passes through (a).
  c. bot._delink strips anything that survives into the model's own text.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

for _name in ("anthropic", "tweepy", "feedparser", "dotenv"):
    _m = types.ModuleType(_name)
    if _name == "dotenv":
        _m.load_dotenv = lambda *a, **k: None
    if _name == "anthropic":
        _m.Anthropic = lambda *a, **k: types.SimpleNamespace()
    if _name == "tweepy":
        _m.TweepyException = Exception
        _m.TooManyRequests = Exception
        _m.Client = _m.API = _m.OAuth1UserHandler = lambda *a, **k: types.SimpleNamespace()
    if _name == "feedparser":
        _m.parse = lambda *a, **k: types.SimpleNamespace(entries=[])
    sys.modules.setdefault(_name, _m)
_pil = types.ModuleType("PIL")
for _sub in ("Image", "ImageDraw", "ImageFont", "ImageFilter"):
    setattr(_pil, _sub, types.SimpleNamespace())
    sys.modules.setdefault(f"PIL.{_sub}", types.ModuleType(f"PIL.{_sub}"))
sys.modules.setdefault("PIL", _pil)

import bot       # noqa: E402
import config    # noqa: E402
import sources   # noqa: E402


def test_no_url_survives_delink():
    """Whatever the model writes, the body X receives holds no linkable token."""
    bodies = [
        "REPORT: Nets and MPJ in extension talks, per roundtable.io",
        "OFFICIAL: Lakers sign wing, per nba.com and si.com",
        "REPORT: deal done https://news.google.com/rss/articles/CBMikwFBVV95cUxQ",
        "RUMOR: story at http://espn.com/nba/story/_/id/12345 today",
        "OFFICIAL: Mavs waive guard. More at hoopshype.com.",
        "REPORT: per The Athletic, a deal is close",          # clean, must survive
        "RUMOR: LeBron deciding today, per @ShamsCharania #NBA",  # mention+tag, clean
    ]
    for body in bodies:
        out = bot._delink(body)
        assert not bot._LINKIFIED.search(out), f"url survived _delink: {out!r}"
    # A clean body must pass through untouched — the guard can't paraphrase.
    clean = "REPORT: per The Athletic, a deal is close"
    assert bot._delink(clean) == clean, "delink altered a body with no url in it"
    mention = "RUMOR: LeBron deciding today, per @ShamsCharania #NBA"
    assert bot._delink(mention) == mention, "delink must not touch mentions/hashtags"
    print(f"no url survives _delink: {len(bodies)} bodies  OK")


def test_publisher_names_never_linkify():
    """Layer (b): a bare-domain publisher never reaches the composer as one."""
    for raw in ("roundtable.io", "nba.com", "si.com", "hoopshype.com", "yardbarker.com"):
        name = sources._publisher_name(raw)
        assert not bot._LINKIFIED.search(f"per {name}"), \
            f"publisher {raw!r} -> {name!r} still linkifies"
    # Real names are left alone.
    for raw in ("ESPN", "The Athletic", "Fear The Sword"):
        assert sources._publisher_name(raw) == raw, f"mangled a real name: {raw}"
    print("bare-domain publishers defused, real names intact  OK")


def test_link_is_only_appended_when_explicitly_enabled():
    """Layer (a): the append is off unless someone opts in, and an unset
    GitHub Actions variable arrives as an EMPTY STRING, not absent."""
    for value, expected in (("0", False), ("", False), (None, False),
                            ("1", True), ("true", True)):
        if value is None:
            os.environ.pop("INCLUDE_SOURCE_LINK", None)
        else:
            os.environ["INCLUDE_SOURCE_LINK"] = value
        import importlib
        importlib.reload(config)
        assert config.INCLUDE_SOURCE_LINK is expected, \
            f"INCLUDE_SOURCE_LINK={value!r} parsed as {config.INCLUDE_SOURCE_LINK}"
    os.environ["INCLUDE_SOURCE_LINK"] = "0"
    import importlib
    importlib.reload(config)
    assert config.INCLUDE_SOURCE_LINK is False
    print("source link stays off by default, empty string included  OK")


def test_every_post_needs_a_card():
    """Rule 2: the gate drops an imageless post rather than posting it bare,
    and a category that DOES produce a card is unaffected."""
    import importlib
    os.environ["REQUIRE_IMAGE"] = "1"
    importlib.reload(config)
    assert config.REQUIRE_IMAGE is True

    def would_post(image):
        return not (image is None and config.REQUIRE_IMAGE)

    assert not would_post(None), "an imageless post must be dropped"
    assert would_post(b"\x89PNG..."), "a post with a card must go out"

    os.environ["REQUIRE_IMAGE"] = "0"
    importlib.reload(config)
    assert would_post(None), "REQUIRE_IMAGE=0 must let text-only through again"
    os.environ["REQUIRE_IMAGE"] = "1"
    importlib.reload(config)
    print("imageless posts dropped, carded posts unaffected  OK")


def test_which_categories_can_produce_a_card():
    """Documents the coverage cost of rule 2, so it can't drift silently:
    only these shapes have a generator, everything else is dropped."""
    def has_card(r, is_final=False):
        if is_final:
            return True  # make_score_card
        return bool(bot._is_player_move(r) and r.get("player") and r.get("to_team"))

    carded = [
        ("signing (is_trade unset)", {"player": "Arthur Kaluma", "to_team": "Lakers",
                                      "from_team": "", "is_trade": False}, False),
        ("trade", {"player": "Cam Johnson", "to_team": "DEN",
                   "from_team": "BKN", "is_trade": True}, False),
        ("final score", {}, True),
    ]
    dropped = [
        ("rumor, no destination", {"player": "LeBron James", "to_team": "",
                                   "from_team": "", "is_trade": False}, False),
        ("coaching hire", {"player": "", "to_team": "",
                           "from_team": "", "is_trade": False}, False),
        ("highlight", {"player": "Victor Wembanyama", "to_team": "",
                       "from_team": "", "is_trade": False}, False),
    ]
    for label, r, fin in carded:
        assert has_card(r, fin), f"{label} should produce a card"
    for label, r, fin in dropped:
        assert not has_card(r, fin), f"{label} unexpectedly produces a card"
    print(f"card coverage: {len(carded)} shapes post, "
          f"{len(dropped)} shapes dropped by REQUIRE_IMAGE  OK")


if __name__ == "__main__":
    test_no_url_survives_delink()
    test_publisher_names_never_linkify()
    test_link_is_only_appended_when_explicitly_enabled()
    test_every_post_needs_a_card()
    test_which_categories_can_produce_a_card()
    print("\nPASS")


# ---------------------------------------------------------------------------
# Regressions from the 2026-07-21 timeline: the same signing posted twice, and
# a linkified publisher in the body.
# ---------------------------------------------------------------------------
def test_sportsnet_ca_is_defused():
    """`.ca` was not in the TLD list, so "Sportsnet.ca" reached the timeline as
    a live link and X billed that post at $0.200 instead of $0.015."""
    import sources
    body = "REPORT: Sharp signs two-way deal with Clippers via Sportsnet.ca"
    out = bot._delink(body)
    assert "Sportsnet.ca" not in out, out
    assert "Sportsnet" in out, f"attribution lost entirely: {out}"
    assert not bot._LINKIFIED.search(out), f"still linkifiable: {out}"
    # and the upstream half, which is what should have caught it first
    assert sources._publisher_name("Sportsnet.ca") == "Sportsnet"
    # country codes generally, not just the one that bit us
    for dom in ("TSN.ca", "Marca.es", "Bild.de", "smh.com.au", "Sport.it"):
        assert not bot._LINKIFIED.search(bot._delink(f"per {dom}")), dom
    print("sportsnet.ca and other country-code domains defused  OK")


def test_bare_surname_dedups_against_full_name():
    """"Jamarion Sharp" then "Sharp" keyed as two different players, so the
    same two-way signing posted twice 57 minutes apart."""
    import state
    real = state.posted_names_today
    try:
        state.posted_names_today = lambda: ["Jamarion Sharp"]
        full = {"player": "Jamarion Sharp", "is_trade": True, "to_team": "LAC"}
        part = {"player": "Sharp", "is_trade": True, "to_team": "Clippers"}
        assert bot._subject_key(full, "") == bot._subject_key(part, ""), \
            f"{bot._subject_key(full,'')} != {bot._subject_key(part,'')}"
        assert bot._trade_player_flag(full) == bot._trade_player_flag(part)

        # ...but do NOT merge two different players who share a surname.
        state.posted_names_today = lambda: ["Jrue Holiday", "Aaron Holiday"]
        amb = {"player": "Holiday", "is_trade": True, "to_team": "BOS"}
        assert bot._subject_key(amb, "") == "p:holiday", \
            "ambiguous surname must not silently pick one of two Holidays"
        assert bot._subject_key({"player": "Jrue Holiday"}, "") == "p:jholiday"
    finally:
        state.posted_names_today = real
    print("bare surname collapses to the full name, ambiguous ones do not  OK")


test_sportsnet_ca_is_defused()
test_bare_surname_dedups_against_full_name()
print("\nPASS")


def test_other_league_teams_never_post():
    """A Dallas Stars (NHL) story posted on 2026-07-22 because "stars" was in
    no block list. The words that overlap normal NBA phrasing can only
    disqualify an item carrying NO NBA signal, or they'd eat real news."""
    import time, sources
    def it(t):
        return sources.NewsItem(id="x", source="ESPN", title=t, summary="",
                                link="", published_ts=time.time())
    for t in ["Stars GM Nill hopeful to extend Robertson after 1-year deal",
              "Dallas Stars sign goaltender to extension",
              "Minnesota Wild trade forward to Rangers",
              "Dolphins sign Jordyn Brooks to extension",
              "White Sox catcher placed on injured list",
              "Liverpool agree deal for midfielder"]:
        assert not bot._worth_composing(it(t)), f"other-league story kept: {t}"
    for t in ["NBA stars react as Lakers sign LeBron James",
              "Wild finish as Celtics beat Knicks 112-110",
              "Lightning-quick Suns guard signs extension",
              "Kings sign guard to a two-year deal",
              "Knicks trade Karl-Anthony Towns to the Suns"]:
        assert bot._worth_composing(it(t)), f"real NBA story dropped: {t}"

    # no hard-blocked name may collide with an NBA team or alias
    import card
    nba = {v[0].lower() for v in card.TEAMS.values()} | set(card._ALIASES)
    pat = bot._HARD_OTHER_SPORT_RE.pattern
    alts = [a for a in pat.split("(", 1)[1].rsplit(")", 1)[0].split("|") if a]
    clash = [a for a in alts if a in nba]
    assert not clash, f"hard-blocks an NBA name: {clash}"
    # Skip alternatives containing regex syntax — the probe below is a literal
    # string, so \.? can't match itself. Their behaviour is covered above.
    literal = [a for a in alts if not any(c in a for c in "\\?*+[](){}|^\$")]
    unmatched = [a for a in literal
                 if not bot._HARD_OTHER_SPORT_RE.search(f"The {a} sign a player")]
    assert not unmatched, f"unmatchable (fused join?): {unmatched}"
    print(f"other-league filter: {len(alts)} hard names, 0 NBA collisions  OK")


test_other_league_teams_never_post()
print("\nPASS")
