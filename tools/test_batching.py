"""Integration test for the batched run_cycle on the CURRENT code.

Real bot.py / composer.py. Stubbed: Claude client, Redis, X. Nothing posts.
"""
import json, sys, types, time

sys.path.insert(0, "/Users/devonyuan/Projects/nba-news-bot")
import bot, composer, config, sources, state, tweeter

config.REQUIRE_IMAGE = False          # isolate batching from card generation
config.MAX_POSTS_PER_DAY = 0
config.SKIP_COVERED_SUBJECTS = True

# ---- in-memory state ------------------------------------------------------
SEEN, POSTS, PLAYERS = set(), [], {}
BUDGET = {"day": 0, "hour": 0}
state.mark_seen = lambda k: SEEN.add(k)
state.is_seen = lambda k: k in SEEN
state.posts_today = lambda: len(POSTS)
state.incr_posts = lambda: None
state.record_post = lambda *a, **k: None
state.player_posts_today = lambda s: PLAYERS.get(s, 0)
state.incr_player_posts = lambda s: PLAYERS.__setitem__(s, PLAYERS.get(s, 0) + 1)
state.highlights_today = lambda: 0
state.incr_highlights = lambda: None
state.get_flag = lambda k: False
state.set_flag = lambda k, ttl=None: None
state.set_feed_health = lambda h: None
state.today_key = lambda: "2026-07-21"
NAMES = []                     # drives _covered_subject_in
state.posted_names_today = lambda: list(NAMES)
state.claude_calls_today = lambda: BUDGET["day"]
state.claude_calls_this_hour = lambda: BUDGET["hour"]
state.incr_claude_calls = lambda n=1: BUDGET.__setitem__("day", BUDGET["day"] + n)
state.incr_claude_calls_hour = lambda n=1: BUDGET.__setitem__("hour", BUDGET["hour"] + n)
tweeter.post = lambda text, image=None: (POSTS.append(text), True)[1]
bot.time.sleep = lambda s: None

# ---- synthetic feed -------------------------------------------------------
now = time.time()
def item(n, title):
    return sources.NewsItem(id=f"id{n}", source="ESPN", title=title,
                            summary=f"Summary for {title}", link=f"http://x/{n}",
                            published_ts=now - 60)

TITLES = [
    "Lakers trade LeBron James to Warriors",
    "Celtics sign Marcus Smart to a two-year extension",
    "Nuggets waive John Smith",
    "Heat acquire Duncan Robinson in a deal",
    "Suns fired head coach Mike Budenholzer",
    "Bulls sign Coby White to an extension",
]
ITEMS = [item(n, t) for n, t in enumerate(TITLES)]
sources.fetch_all = lambda: list(ITEMS)

# ---- stubbed Claude -------------------------------------------------------
CALLS = []
def blank():
    return {k: ("" if v.get("type") == "string" else 0 if v.get("type") == "integer" else False)
            for k, v in composer.TWEET_SCHEMA["properties"].items()}

def fake_create(**kw):
    CALLS.append(kw)
    msg = kw["messages"][0]["content"]
    heads = [l[len("Headline: "):] for l in msg.splitlines() if l.startswith("Headline: ")]
    res = []
    for n, h in enumerate(heads):
        r = blank(); r["index"] = n
        # Only LeBron is newsworthy — proves the verdict tracks the headline.
        if "LeBron" in h:
            r.update(newsworthy=True, category="report", tweet="LeBron to GSW",
                     player="LeBron James")
        else:
            r.update(newsworthy=False, category="skip", tweet="")
        res.append(r)
    payload = {"results": res} if len(res) > 1 else res[0]
    blk = types.SimpleNamespace(type="text", text=json.dumps(payload))
    return types.SimpleNamespace(content=[blk], stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=100, output_tokens=50,
            cache_read_input_tokens=0, cache_creation_input_tokens=0))

composer.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=fake_create))

# ===========================================================================
print("--- 1. normal cycle: all 6 items, one call ---")
bot.run_cycle()
assert len(CALLS) == 1, f"expected 1 batched call, got {len(CALLS)}"
assert CALLS[0]["messages"][0]["content"].count("Headline:") == 6
assert POSTS == ["LeBron to GSW"], POSTS
assert BUDGET["day"] == 6, BUDGET
print(f"OK 1 call for 6 items | posted {POSTS} | budget charged {BUDGET['day']} items\n")

# ===========================================================================
print("--- 2. budget-held items are NOT dropped (the old bug) ---")
SEEN.clear(); POSTS.clear(); PLAYERS.clear(); CALLS.clear(); NAMES.clear()
BUDGET.update(day=0, hour=0)
config.MAX_CLAUDE_ITEMS_PER_HOUR = 2      # only 2 affordable this hour
bot.run_cycle()
assert len(CALLS) == 1, CALLS

def composed_titles(call):
    msg = call["messages"][0]["content"]
    return {l[len("Headline: "):] for l in msg.splitlines() if l.startswith("Headline: ")}

sent = composed_titles(CALLS[0])
assert len(sent) == 2, f"should compose exactly 2, sent {len(sent)}"
composed_ids = {i.id for i in ITEMS if i.title in sent}
held_ids = {i.id for i in ITEMS} - composed_ids
assert composed_ids <= SEEN, "composed items must be marked seen"
assert not (held_ids & SEEN), \
    f"HELD ITEMS WERE MARKED SEEN (dropped forever): {sorted(held_ids & SEEN)}"
print(f"OK composed 2, held {len(held_ids)} — and every held item is still unseen")

# next hour: budget frees up, the held items come back
BUDGET["hour"] = 0
CALLS.clear()
bot.run_cycle()
assert len(CALLS) == 1, CALLS
assert CALLS[0]["messages"][0]["content"].count("Headline:") == 2
print("OK next hour picked 2 of the held items back up (news not lost)\n")

# ===========================================================================
print("--- 3. daily budget exhausted -> zero spend ---")
SEEN.clear(); POSTS.clear(); PLAYERS.clear(); CALLS.clear()
config.MAX_CLAUDE_ITEMS_PER_HOUR = 100
BUDGET.update(day=config.MAX_CLAUDE_ITEMS_PER_DAY, hour=0)
bot.run_cycle()
assert len(CALLS) == 0, f"spent {len(CALLS)} call(s) with the day's budget gone"
assert not SEEN, "items burned while out of budget — they must come back tomorrow"
print("OK no call made, and nothing marked seen\n")

# ===========================================================================
print("--- 4. batch splits at CLAUDE_BATCH_SIZE ---")
SEEN.clear(); POSTS.clear(); PLAYERS.clear(); CALLS.clear()
BUDGET.update(day=0, hour=0)
config.CLAUDE_BATCH_SIZE = 2
bot.run_cycle()
assert len(CALLS) == 3, f"6 items / batch 2 should be 3 calls, got {len(CALLS)}"
assert BUDGET["day"] == 6, BUDGET
print(f"OK 6 items at batch-size 2 -> {len(CALLS)} calls, budget charged {BUDGET['day']}\n")

# ===========================================================================
print("--- 5. seen items are never re-composed ---")
CALLS.clear()
bot.run_cycle()
assert len(CALLS) == 0, f"re-composed seen items: {len(CALLS)} calls"
print("OK second pass over the same feed spent nothing\n")

print("ALL CYCLE TESTS PASSED")
