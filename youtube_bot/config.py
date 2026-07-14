"""Configuration for the YouTube Shorts story bot.

Loads from .env / environment. Reuses the same ANTHROPIC_API_KEY and Upstash
state store as the NBA bot so you only manage one set of secrets.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Claude (story + metadata generation) ---------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-opus-4-8"

# --- Voice / TTS -----------------------------------------------------------
# "edge" = free Microsoft Edge neural voices (no key). "elevenlabs" = paid.
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge").strip().lower()
EDGE_VOICE = os.getenv("EDGE_VOICE", "en-US-AndrewNeural")   # good storytelling voice
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY", "")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID", "")

# --- YouTube upload --------------------------------------------------------
# Path to the OAuth client secrets json downloaded from Google Cloud Console,
# and the token cache created after the first (one-time) manual authorization.
YT_CLIENT_SECRETS = os.getenv("YT_CLIENT_SECRETS", "youtube_bot/client_secret.json")
YT_TOKEN_FILE = os.getenv("YT_TOKEN_FILE", "youtube_bot/yt_token.json")
YT_PRIVACY = os.getenv("YT_PRIVACY", "public")   # public | unlisted | private
YT_CATEGORY_ID = os.getenv("YT_CATEGORY_ID", "24")  # 24 = Entertainment

# --- Content / topic -------------------------------------------------------
# Rotation of story styles so consecutive videos don't feel same-y. Each run
# picks one at random. Override the whole set by setting STORY_THEME in the env
# (single theme) — that takes priority over the rotation if provided.
STORY_THEMES = [
    "a suspenseful first-person Reddit-style confession story with a shocking twist ending",
    "a creepy true-crime-style micro horror story with an unsettling final line",
    "an 'Am I The Asshole' style family/relationship drama story that ends by asking viewers to judge",
    "a bizarre-but-believable 'you won't believe what happened to me' story with a wild payoff",
    "a 'would you rather' moral dilemma story that ends by asking viewers to comment their choice",
    "a fascinating little-known historical or science fact told as a punchy 45-second story",
]
# Single-theme override (optional). If set, always uses this instead of the rotation.
STORY_THEME = os.getenv("STORY_THEME", "").strip()
# Target spoken length in seconds (Shorts sweet spot ~35-55s).
TARGET_SECONDS = int(os.getenv("TARGET_SECONDS", "45"))

# --- Behavior --------------------------------------------------------------
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
MAX_UPLOADS_PER_DAY = int(os.getenv("MAX_UPLOADS_PER_DAY", "4"))

# --- Paths -----------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKGROUNDS_DIR = os.path.join(ROOT, "backgrounds")
OUTPUT_DIR = os.path.join(ROOT, "output")

# --- State (shared with NBA bot) ------------------------------------------
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN", "")
