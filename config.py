"""Configuration loaded from .env / environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# The tweet-writer only classifies an item and rewrites it to <=250 chars —
# Haiku handles that easily at a fraction of Opus's cost (this is the bot's
# only pay-per-use API, so the model choice is essentially the whole bill).
# Override with ANTHROPIC_MODEL in the environment to go back to a bigger model.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# ---- Insider X accounts (insiders.py) --------------------------------------
# Read the scoop from the tweet instead of waiting for an outlet to write it up
# and Google to index it — that chain runs 5-20 minutes behind, which on a trade
# is the whole difference between first and forty-first.
#
# COST: X bills third-party reads at $0.005 per RESOURCE RETURNED. The bill is
# therefore set by how much these accounts POST, not by how often we poll, and
# insiders.py uses `since_id` so an idle poll returns nothing and costs nothing.
#
# Two accounts, not four. Shams breaks the most; Stein is independent and owns
# coaching and front-office moves, which is the category Shams covers least — so
# he adds the most per dollar. Haynes and Fischer overlap Shams on player
# transactions, and paying $0.005 to hear the same scoop twice is not worth it.
# Estimated ~25 originals/day between them, ~$0.125/day, ~$3.75/month.
INSIDER_X_ENABLED = os.getenv("INSIDER_X_ENABLED", "1").strip().lower() in ("1", "true", "yes")
INSIDER_X_ACCOUNTS = [
    h.strip().lstrip("@") for h in
    os.getenv("INSIDER_X_ACCOUNTS", "ShamsCharania,TheSteinLine,ChrisBHaynes").split(",")
    if h.strip()
]
# Ceiling on tweets pulled per account per poll. A thread-storm is the only way
# this module can spike, and this is the cap on that: 10 tweets = $0.05 worst
# case per account per cycle, and anything above it is picked up next poll.
INSIDER_X_MAX_PER_POLL = int(os.getenv("INSIDER_X_MAX_PER_POLL") or 10)

# How often to poll the insider accounts, independent of POLL_SECONDS.
#
# 30s against RSS's 90s, because the two are limited by different things and
# there is no reason to hold the fast one back to protect the slow one.
#
# Google News throttles on REQUEST RATE — 60s polling got the runner's IP
# blocked for four hours on 2026-07-21, costing six of eight sources — so RSS
# needs a polite cadence. X bills on tweets RETURNED, and `since_id` means an
# idle poll returns none: three times as many idle polls is three times $0.00.
# What it buys is the thing worth buying, since these accounts break the news
# the articles are later written about: average detection lag on a Shams scoop
# drops from ~45s to ~15s.
#
# Rate limits are the real ceiling here, not cost. Two accounts at 30s is 240
# requests/hour, comfortably inside the v2 user-timeline allowance, and
# insiders.py already treats a 429 as "skip this cycle" rather than an error.
INSIDER_POLL_SECONDS = int(os.getenv("INSIDER_POLL_SECONDS") or 30)

X_API_KEY = os.getenv("X_API_KEY", "")
# App-only bearer token. /2/users/:id/tweets is happiest with app auth; tweepy
# falls back to the OAuth1 user context below when this is unset.
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
# Detection lag averages half this, so it is the single biggest lever on how
# fast a scoop goes out. 60s is affordable now that fetch_all sweeps all 14
# feeds concurrently (~1s, was ~7.4s serial) — the cycle is nearly all sleep.
# Raise it if the dashboard's feed-health view starts showing errors: polling
# Google News harder risks throttling, which costs more latency than it buys.
# `or 90` not a default: an unset GitHub Actions variable arrives as an EMPTY
# STRING, not absent, and int("") raises — the same trap MAX_POSTS_PER_DAY hit.
#
# Back to 90 from 60 on 2026-07-21. Polling twice as often does not just cost
# more requests — the Google News feeds are windowed (`when:1h`), so a shorter
# interval surfaces items that would otherwise appear and roll out between
# polls. Each extra item is a paid Claude call, and Jul 21 spent ~$1.50 of
# Anthropic credit in four hours against ~$1.22 for all of Jul 19. Detection
# lag averages half this, so 90s costs ~15s of latency against a real bill.
#
# Back to 60 later the same day, because both halves of that reasoning changed.
# An extra item is now $0.00048, not $0.00205 — the cost objection is 4x weaker
# — and an item over the hourly allowance is now HELD rather than marked seen
# and dropped, so surfacing more of them can no longer lose news. 60s buys back
# ~15s of detection lag at 1.5x the fetch load.
#
# Not 30s, which is the other thing worth wanting: that is 3x the fetch load
# against 90, and the note above still stands that pulling Google News harder
# risks throttling — which COSTS coverage rather than buying it. 60 is known
# good; it ran this morning. Try 30 only after a day of clean feed health.
#
# BACK TO 90 the same evening, because 60 did exactly what that warning said.
# All six Google News feeds were healthy at 18:13 UTC and all six were `down`
# by 22:08, while the identical URLs returned 50+ entries from a laptop — so it
# was the runner's IP being blocked, not the feeds. 60s over six Google queries
# is ~360 requests/hour from one address; it was tolerated for about two hours
# and then it was not.
#
# The 15s of detection lag 60 bought is worthless against six dead feeds. Do
# not lower this again without watching feed health for a full day: the failure
# is silent from the bot's side (a blocked fetch looks exactly like a quiet
# news hour) and it costs total coverage, not latency.
POLL_SECONDS = int(os.getenv("POLL_SECONDS") or 90)

# Hard ceiling on paid Claude calls per UTC day. This is a spend cap, not an
# editorial one: it counts compose() ATTEMPTS, because a call costs the same
# whether the item is posted or discarded by the dedup that runs after it.
# 0 = uncapped. Roughly $0.0025 a call, so 150 is about $0.38/day, $11/month.
#
# Sized against what the bot actually does, not what it fetches. Measured on
# 2026-07-21: a 5h45m run made 113 calls (236,599 in / 10,690 out) — about 20
# an hour, ~470/day uncapped, ~$36/month. Against MAX_POSTS_PER_DAY=10 that is
# ~47 paid calls per published post: almost everything compose() is paid for is
# then thrown away by the freshness, per-subject and trade-dedup checks that
# run after it. 150 still leaves ~15 calls per post, which is ample headroom;
# the honest fix is to move those checks in front of compose() so the cap stops
# being the thing that bounds the bill.
#
# Sized to a BUDGET, not to demand. Owner topped up $5 on 2026-07-21 and needs
# it to last a month, which is ~$0.17/day, which is ~66 calls. 150 would have
# drained it in 13 days. This will bind well before the news does, so on a busy
# day the bot goes quiet once the budget is spent — that is the intended
# behaviour, and it is why the cap is an env var: raise it the moment there is
# more credit, and see the calls-per-post note above for the real fix.
# 72, not 66, so that 24 hours x 3 calls divides exactly. At 66 the hourly
# allowance still rounded to 3 and the day ran dry around hour 22 — the account
# went dark for the last stretch of every day for no benefit, since 72 costs
# $0.166/day against 66's $0.152 and both round to ~$5/month.
#
# 72 -> 340 (2026-07-21), same money. The counter always measured ITEMS
# composed, not HTTP requests, and composer.compose_batch now puts 25 items in
# one request: measured on the live feeds that is $0.00205 -> $0.00048 per item,
# 4.2x cheaper, so $0.166/day buys ~344 headlines instead of ~81. (4.2x and not
# 12x because output tokens — ~70/item at 5x the input rate — do not amortise.)
#
# 340 was chosen to spend the SAME $0.166/day, not more. It is above what the
# free prefilters currently pass, so in practice the bot now reads everything
# that reaches it rather than rationing 3 headlines an hour and dropping the
# rest of the news that broke in between.
#
# 340 -> 500 (2026-07-23, owner's call). On a busy news day 340 was spent by
# early afternoon — with the evening reserve holding 100 back, the daytime 240
# ran dry around 1pm ET and the account went quiet until 6pm. 500 gives 400 for
# the day and 100 for the evening, which covers a full busy day at the observed
# ~18 items/hour without going dark in the middle. This is a CEILING, not a
# floor: the bot only pays for items it actually composes, so a quiet day still
# costs less. At $0.00048/item the ceiling moves $0.163 -> $0.240/day (~$2.30 a
# month more), Claude only — the X bill is separate.
MAX_CLAUDE_ITEMS_PER_DAY = int(
    os.getenv("MAX_CLAUDE_ITEMS_PER_DAY") or os.getenv("MAX_CLAUDE_CALLS_PER_DAY") or 500
)
# Old name kept so an existing MAX_CLAUDE_CALLS_PER_DAY repo variable, and any
# code still reading it, keep working against the renamed knob.
MAX_CLAUDE_CALLS_PER_DAY = MAX_CLAUDE_ITEMS_PER_DAY

# Hours a day the budget should stretch across. The daily cap alone protects
# the balance but not the coverage: on 2026-07-21 the bot spent $0.12 of its
# $0.15 daily budget inside an hour, so it would have gone silent before
# lunchtime and missed every afternoon signing. Pacing turns "66 calls, then
# nothing" into "roughly 4 an hour, all day".
#
# The hourly allowance is derived, not configured, so raising the daily cap
# widens each hour rather than letting the whole thing burn at once. Unused
# hours do NOT roll over — that is the point; a quiet morning must not fund a
# spike that empties the day by noon.
# Spread the daily budget over this many hours. 24 gives 14 items/hour, which
# on 2026-07-22 became the binding constraint rather than the safety net: the
# bot sat at 14/14 for most of the day while real news went unread, and a
# prompt fix could not even be evaluated because nothing new could be composed
# until the clock rolled over.
#
# 12 gives ~28/hour. The DAILY cap is unchanged, so this cannot spend more
# money — it only lets the bot catch up inside an hour instead of trickling.
# The original worry (burning the day by lunchtime) is still covered by the
# daily ceiling, which is the thing that actually protects the balance.
CLAUDE_SPEND_HOURS = int(os.getenv("CLAUDE_SPEND_HOURS") or 12)
MAX_CLAUDE_ITEMS_PER_HOUR = max(1, MAX_CLAUDE_ITEMS_PER_DAY // max(1, CLAUDE_SPEND_HOURS))
MAX_CLAUDE_CALLS_PER_HOUR = MAX_CLAUDE_ITEMS_PER_HOUR  # old name, see above

# Items of the daily cap that may only be spent in the evening.
#
# The cap decides HOW MUCH is spent; nothing decided WHEN. With the day turning
# over at midnight ET and 12 spend-hours at ~28 items each, a day running at
# rate is empty by mid-afternoon — which is exactly what happened on
# 2026-07-22: last post 13:01 ET, budget gone at 16:46 ET, and the account
# silent through the 7-11pm window that dashboard_data's own learning note
# calls the best-performing one of the day. The blackout was structural, not
# bad luck; every day is shaped that way.
#
# This spends no extra money. It fences off part of the SAME cap: before
# CLAUDE_EVENING_HOUR only (cap - reserve) is available, after it the rest
# unlocks. 100 of 340 leaves 240 for the 18 hours to 6pm and guarantees the
# evening 100 no matter how busy the morning was. The hourly pace still
# applies on top, so the reserve is drawn down over the evening rather than
# dumped in one cycle. Set CLAUDE_EVENING_RESERVE=0 to go back to first-come
# first-served.
CLAUDE_EVENING_RESERVE = int(os.getenv("CLAUDE_EVENING_RESERVE") or 100)
CLAUDE_EVENING_HOUR = int(os.getenv("CLAUDE_EVENING_HOUR") or 18)

# How many items ride in one Claude request. This is what makes the ceiling
# above affordable — see composer.compose_batch. Bigger is cheaper per item but
# raises the blast radius of a malformed reply and gives the model a longer list
# to stay sharp across; 25 is the tested balance, and anything the batch fails
# to answer for is retried individually, so raising it trades money for
# coverage, never the reverse.
CLAUDE_BATCH_SIZE = int(os.getenv("CLAUDE_BATCH_SIZE") or 25)

# Require the HEADLINE to assert an event before paying to compose it — see
# bot._EVENT_RE. This is the difference between spending the daily budget on
# 66 headlines and spending it on 66 STORIES: measured on a live cycle it cuts
# what reaches the paid call by ~53% while keeping every transaction and all
# the free-agency chatter. The commentary layer (summer league grades, "why X
# was positive", scout takes) is what goes.
#
# On by default because the budget forces it. Set REQUIRE_NEWS_EVENT=0 to go
# back to the old "when in doubt, let it through" behaviour once there is
# credit to spend on maybes.
REQUIRE_NEWS_EVENT = os.getenv("REQUIRE_NEWS_EVENT", "1").strip().lower() in ("1", "true", "yes")

# Skip an item before paying for it when its headline names a player already
# posted about today. MAX_POSTS_PER_PLAYER already rejects these — but only
# after the Claude call, because the rule needs the player name the call
# returns. One live cycle carried ten items about the same Rich Paul / LeBron
# story: ten calls bought one post.
#
# This buys back budget without costing coverage, because the duplicate was
# never going to be published either way. It is not free of risk: an item that
# merely MENTIONS a covered player is skipped too, so a genuine Anthony Davis
# story that leads with LeBron's name is lost. Every skip is logged with the
# name that matched, so the cost is auditable rather than silent.
SKIP_COVERED_SUBJECTS = os.getenv("SKIP_COVERED_SUBJECTS", "1").strip().lower() in ("1", "true", "yes")
# Daily post cap. 0 = uncapped.
# CORRECTION (2026-07-21): this used to say posting cost nothing, because reads
# ran in the hundreds of requests/day against 5-14 for posts. That read the
# wrong meter. Reads bill per RESOURCE returned, not per request, while a post
# containing a url bills at $0.200 — so posting was 42% of the bill and the
# largest single line. Posts are ~$0.015 each now that no url goes out (see
# INCLUDE_SOURCE_LINK), so the cap is again about editorial volume, not cost.
# Set MAX_POSTS_PER_DAY to a positive number to put the cap back.
# `or 0` not a default: an unset GitHub Actions variable arrives as an EMPTY
# STRING, not as absent, and int("") raises. Empty means uncapped.
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY") or 0)

# Standout-performance highlights (summer league + regular season) are capped
# separately and only posted for genuine stars / top prospects. They're the
# lowest-value posts, so they're kept few to save X API writes.
MAX_HIGHLIGHTS_PER_DAY = int(os.getenv("MAX_HIGHLIGHTS_PER_DAY", "4"))

# Backstop, not a throttle: the most items about ONE subject (player, or the
# teams involved when no player is named) that may post in a day. There is no
# total post cap by design, so this is what bounds a story that slips past the
# semantic dedup.
#
# ONE, not two. Set at 2 initially and that was wrong: six outlets rewriting the
# Thybulle signing still produced two posts, and two duplicates is still
# duplicates. A second item about the same subject on the same day is almost
# always another outlet's rewrite, not new information. Distinct players and
# teams never compete for this, so real coverage is untouched.
MAX_POSTS_PER_PLAYER = int(os.getenv("MAX_POSTS_PER_PLAYER", "1"))

# Evergreen debate cards ("What team is one move away?", "Keep 3, cut the rest").
# Off by owner's call 2026-07-20: they read as filler on the timeline and weren't
# earning replies, which was the whole reason for posting them. The generator is
# untouched — set ENABLE_DEBATE_POSTS=1 to bring them back.
ENABLE_DEBATE_POSTS = os.getenv("ENABLE_DEBATE_POSTS", "0").strip().lower() in ("1", "true", "yes")

# Append the source article's link to each tweet. OFF by default, because X
# prices a post containing ANY url at $0.20 against one at $0.010 — appending
# the link made every post 20x more expensive, and at 10 posts/day that was
# ~$2.00/day (~$60/mo) versus ~$0.10/day (~$3/mo) without it. It was 42% of the
# entire X bill for 2026-06-21..07-21. Attribution does not depend on this: the
# composer already names the outlet in the tweet text ("per ESPN"), and the
# links being appended were opaque news.google.com/rss/articles/... redirects.
# Set INCLUDE_SOURCE_LINK=1 to bring them back, knowing the 20x cost.
INCLUDE_SOURCE_LINK = os.getenv("INCLUDE_SOURCE_LINK", "0").strip().lower() in ("1", "true", "yes")

# Every post carries a graphic or it doesn't go out at all. Owner's call
# 2026-07-21: a bare text post reads as a scraper, and the card is the whole
# visual identity of the account. It costs nothing to insist on — X bills
# image+text at the same $0.015 as text alone.
#
# This DOES cost coverage, and knowingly: only player moves (trade card) and
# finished games (score card) have a generator, so items with no card are
# dropped rather than posted bare — rumors with no destination named, coaching
# and front-office moves, and highlights that aren't final scores. Set
# REQUIRE_IMAGE=0 to let those post as text again, or add a generator for them
# to card.py and they start qualifying automatically.
REQUIRE_IMAGE = os.getenv("REQUIRE_IMAGE", "1").strip().lower() in ("1", "true", "yes")

# How recent (in minutes) an item must be to still be worth posting. Breaking
# news lives or dies on latency — a game score that's hours old gets no traction,
# so keep this tight. Anything older is dropped instead of posted stale.
FRESH_MAX_AGE_MIN = int(os.getenv("FRESH_MAX_AGE_MIN", "45"))

# Trades, signings, and other roster moves stay newsworthy for hours — people
# still want the alert and the graphic long after a game recap has gone cold — so
# transactions get a much wider window than the tight freshness above. This is
# what lets the bot actually post a trade it catches a few hours after it breaks.
TRADE_MAX_AGE_MIN = int(os.getenv("TRADE_MAX_AGE_MIN", "360"))

# Upstash Redis for cross-run state (shared with the dashboard). Accept both
# the Upstash-native names and Vercel's KV_ prefixed names.
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN", "")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# Feeds ordered roughly by how fast they break news. Each entry:
# (source name shown in attribution, feed URL)
#
# Google News search feeds are first because they aggregate EVERY outlet in
# near real time and support a recency filter (when:) — so a story is caught the
# moment any publisher posts it, instead of waiting for one outlet's own feed to
# refresh. The per-item real publisher is resolved for attribution in
# sources.fetch_all(). The remaining direct feeds are slower backfill.
# The trades query uses a wider when:6h so transaction coverage that trickles out
# over the hours after a deal breaks still surfaces (freshness is then enforced
# per-type in bot.process_item). Injuries stay tight since they're time-sensitive.
_GNEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="

FEEDS = [
    # Trades / signings / roster moves — the highest-engagement breaking news.
    ("Google News", _GNEWS + "NBA%20(trade%20OR%20traded%20OR%20signs%20OR%20%22agrees%20to%22%20OR%20waived%20OR%20claimed)%20when%3A6h"),
    # Injuries / availability / discipline.
    ("Google News", _GNEWS + "NBA%20(injury%20OR%20injured%20OR%20suspended%20OR%20%22ruled%20out%22%20OR%20%22out%20for%22)%20when%3A1h"),
    # Star highlights / standout performances (summer league + regular season).
    # The composer filters these down to genuine stars / top prospects.
    # dropped 2026-07-21 — summer-league noise only; 1 paid call, 0 transactions
    # ("Google News", _GNEWS + "NBA%20(%22career-high%22%20OR%20%22triple-double%22%20OR%20%22summer%20league%22%20OR%20%22game-winner%22%20OR%20%2240%20points%22%20OR%20%2250%20points%22)%20when%3A1h"),
    # Extensions / re-signings / buyouts — high-engagement roster moves the
    # trade query's keywords (traded/signs/waived) don't catch.
    ("Google News", _GNEWS + "NBA%20(%22extension%22%20OR%20%22re-signs%22%20OR%20%22re-signing%22%20OR%20%22buyout%22)%20when%3A1h"),
    # Coaching + front-office moves (hirings, firings) — previously uncovered.
    ("Google News", _GNEWS + "NBA%20(%22head%20coach%22%20OR%20%22coaching%20staff%22%20OR%20fired%20OR%20hired%20OR%20hires)%20when%3A1h"),
    # Whatever the top insiders break, however it's phrased — their bylines are
    # the most reliable marker of a real scoop, and this catches stories worded
    # in ways the keyword queries miss ("finalizing a deal", "intends to sign").
    # when:1h -> 3h on 2026-07-21. These four break most NBA news first, so this
    # is the highest-value feed in the list — and at a one-hour window it
    # returned NOTHING on every check, because in any given hour none of them
    # has filed. The scoops were still arriving, just second-hand from
    # aggregators an hour later. Measured at 3h it returns 6 items including
    # "Lakers sign free-agent forward Matisse Thybulle on 1-year, $3.3M" — the
    # signing WITH its contract figure, which is the ideal post. Kept at 3h
    # rather than 12h so the account stays a breaking-news account.
    ("Google News", _GNEWS + "(%22Shams%20Charania%22%20OR%20%22Marc%20Stein%22%20OR%20%22Chris%20Haynes%22%20OR%20%22Jake%20Fischer%22)%20when%3A3h"),
    # Free-agency / trade chatter / decision-watch — the speculation that drives
    # the most engagement ("star reportedly deciding today", "weighing offers",
    # "requested a trade", "suitors"). The keyword feeds above only catch DONE
    # deals; this catches the build-up. The composer still requires a named
    # source and never invents a rumor. Wider when: since chatter lives all day.
    ("Google News", _GNEWS + "NBA%20(%22free%20agency%22%20OR%20%22expected%20to%20sign%22%20OR%20%22expected%20to%20decide%22%20OR%20%22trade%20request%22%20OR%20%22requested%20a%20trade%22%20OR%20%22meeting%20with%22%20OR%20%22in%20talks%22%20OR%20suitors%20OR%20%22market%20for%22)%20when%3A2h"),
    # Just-finished games — result headlines carry the final score, which feeds
    # the FINAL score card. Tight window: a score is only fresh at the buzzer.
    # dropped 2026-07-21 — there are no final scores in the offseason
    # ("Google News", _GNEWS + "NBA%20(beat%20OR%20beats%20OR%20defeats%20OR%20%22final%20score%22%20OR%20%22holds%20off%22)%20when%3A1h"),
    ("RealGM", "https://basketball.realgm.com/rss/wiretap/0/0.xml"),
    # HoopsHype removed 2026-07-21: the feed is gone, not flaky. Every variant
    # (/feed/, /rss/, www, /feed/rss/) 301s to www.hoopshype.com and then 404s
    # with an HTML error page — the site moved onto the Gannett platform and
    # dropped RSS. It had been silently contributing zero items while still
    # showing up as a source in the dashboard's health view, which is worse
    # than being absent: it looked like coverage that wasn't there.
    # ESPN direct feed removed 2026-07-23. It returns ZERO entries to the
    # GitHub Actions runner — a datacenter-IP block, the same family as the
    # Google News one — while serving 16 items to a laptop on a home IP. The
    # browser-header attempt did not defeat it. It sat "down" in the dashboard
    # contributing nothing, and ESPN's reporting reaches the bot through the
    # Google News queries above anyway (their <source> resolves to "ESPN"), so
    # this is dead weight, not lost coverage.
    # ("ESPN", "https://www.espn.com/espn/rss/nba/news"),
    # dropped 2026-07-21 — 3 paid calls, 0 transactions — opinion and recaps
    # ("Yahoo Sports", "https://sports.yahoo.com/nba/rss.xml"),
    # dropped 2026-07-21 — 0 paid, 0 transactions
    # ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
    # dropped 2026-07-21 — 0 paid, 0 transactions; its items are always stale by the time we see them
    # ("SB Nation", "https://www.sbnation.com/rss/nba/index.xml"),
]
