"""Configuration loaded from .env / environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-opus-4-8"

X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() != "false"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "15"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# Feeds ordered roughly by how fast they break news. Each entry:
# (source name shown in attribution, feed URL)
FEEDS = [
    ("ESPN", "https://www.espn.com/espn/rss/nba/news"),
    ("Yahoo Sports", "https://sports.yahoo.com/nba/rss.xml"),
    ("HoopsHype", "https://hoopshype.com/feed/"),
    ("RealGM", "https://basketball.realgm.com/rss/wiretap/0/0.xml"),
    ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
]
