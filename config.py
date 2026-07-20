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

X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))
# Daily post cap. 0 = uncapped, which is the default: the X bill turned out to be
# ~99% reads (the dashboard's 100-post sync), not writes — posting ran 5-14
# requests/day against reads in the hundreds. Capping posts was throttling
# coverage to save money that posting was never spending.
# Set MAX_POSTS_PER_DAY to a positive number to put the cap back.
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "0"))

# Standout-performance highlights (summer league + regular season) are capped
# separately and only posted for genuine stars / top prospects. They're the
# lowest-value posts, so they're kept few to save X API writes.
MAX_HIGHLIGHTS_PER_DAY = int(os.getenv("MAX_HIGHLIGHTS_PER_DAY", "4"))

# Backstop, not a throttle: the most items about ONE player that may post in a
# day. There is no total post cap by design, so this is what bounds a story that
# slips past the semantic dedup — four outlets rewriting the same signing cost
# two posts instead of a timeline. Legitimate coverage is unaffected: distinct
# players are never in competition for this.
MAX_POSTS_PER_PLAYER = int(os.getenv("MAX_POSTS_PER_PLAYER", "2"))

# Evergreen debate cards ("What team is one move away?", "Keep 3, cut the rest").
# Off by owner's call 2026-07-20: they read as filler on the timeline and weren't
# earning replies, which was the whole reason for posting them. The generator is
# untouched — set ENABLE_DEBATE_POSTS=1 to bring them back.
ENABLE_DEBATE_POSTS = os.getenv("ENABLE_DEBATE_POSTS", "0").strip().lower() in ("1", "true", "yes")

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
    ("Google News", _GNEWS + "NBA%20(%22career-high%22%20OR%20%22triple-double%22%20OR%20%22summer%20league%22%20OR%20%22game-winner%22%20OR%20%2240%20points%22%20OR%20%2250%20points%22)%20when%3A1h"),
    # Extensions / re-signings / buyouts — high-engagement roster moves the
    # trade query's keywords (traded/signs/waived) don't catch.
    ("Google News", _GNEWS + "NBA%20(%22extension%22%20OR%20%22re-signs%22%20OR%20%22re-signing%22%20OR%20%22buyout%22)%20when%3A1h"),
    # Coaching + front-office moves (hirings, firings) — previously uncovered.
    ("Google News", _GNEWS + "NBA%20(%22head%20coach%22%20OR%20%22coaching%20staff%22%20OR%20fired%20OR%20hired%20OR%20hires)%20when%3A1h"),
    # Whatever the top insiders break, however it's phrased — their bylines are
    # the most reliable marker of a real scoop, and this catches stories worded
    # in ways the keyword queries miss ("finalizing a deal", "intends to sign").
    ("Google News", _GNEWS + "(%22Shams%20Charania%22%20OR%20%22Marc%20Stein%22%20OR%20%22Chris%20Haynes%22%20OR%20%22Jake%20Fischer%22)%20when%3A1h"),
    # Free-agency / trade chatter / decision-watch — the speculation that drives
    # the most engagement ("star reportedly deciding today", "weighing offers",
    # "requested a trade", "suitors"). The keyword feeds above only catch DONE
    # deals; this catches the build-up. The composer still requires a named
    # source and never invents a rumor. Wider when: since chatter lives all day.
    ("Google News", _GNEWS + "NBA%20(%22free%20agency%22%20OR%20%22expected%20to%20sign%22%20OR%20%22expected%20to%20decide%22%20OR%20%22trade%20request%22%20OR%20%22requested%20a%20trade%22%20OR%20%22meeting%20with%22%20OR%20%22in%20talks%22%20OR%20suitors%20OR%20%22market%20for%22)%20when%3A2h"),
    # Just-finished games — result headlines carry the final score, which feeds
    # the FINAL score card. Tight window: a score is only fresh at the buzzer.
    ("Google News", _GNEWS + "NBA%20(beat%20OR%20beats%20OR%20defeats%20OR%20%22final%20score%22%20OR%20%22holds%20off%22)%20when%3A1h"),
    ("RealGM", "https://basketball.realgm.com/rss/wiretap/0/0.xml"),
    ("HoopsHype", "https://hoopshype.com/feed/"),
    ("ESPN", "https://www.espn.com/espn/rss/nba/news"),
    ("Yahoo Sports", "https://sports.yahoo.com/nba/rss.xml"),
    ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
    ("SB Nation", "https://www.sbnation.com/rss/nba/index.xml"),
]
