"""NBA breaking-news bot: watch RSS feeds, compose tweets with Claude, post to X.

Run:  python bot.py
Stop: Ctrl+C
"""

import json
import os
import sys
import time
from datetime import date

# Windows consoles default to cp1252, which crashes on emoji in headlines/tweets
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import sources
import tweeter
from composer import compose

MAX_SEEN = 2000  # keep the dedup list bounded


def load_state() -> dict:
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen": [], "post_day": "", "post_count": 0}


def save_state(state: dict) -> None:
    state["seen"] = state["seen"][-MAX_SEEN:]
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def posts_remaining_today(state: dict) -> int:
    today = date.today().isoformat()
    if state.get("post_day") != today:
        state["post_day"] = today
        state["post_count"] = 0
    return config.MAX_POSTS_PER_DAY - state["post_count"]


def run_cycle(state: dict) -> None:
    seen = set(state["seen"])
    items = [i for i in sources.fetch_all() if i.id not in seen and sources.is_fresh(i)]

    if not items:
        return
    print(f"{len(items)} new item(s)")

    for item in items:
        # Always mark seen first — a bad item shouldn't be retried forever
        state["seen"].append(item.id)

        if posts_remaining_today(state) <= 0:
            print("Daily post cap reached; skipping remaining items")
            continue

        result = compose(item)
        if not result or not result.get("newsworthy") or not result.get("tweet"):
            print(f"  skipped: {item.title[:70]}")
            continue

        text = result["tweet"].strip()
        if item.link:
            text = f"{text}\n{item.link}"

        if tweeter.post(text):
            state["post_count"] += 1

        save_state(state)
        time.sleep(3)  # small gap between posts, looks less bot-bursty

    save_state(state)


def main() -> None:
    mode = "DRY RUN (printing only)" if config.DRY_RUN else "LIVE (posting to X)"
    print(f"NBA news bot starting — {mode}")
    print(f"Polling {len(config.FEEDS)} feeds every {config.POLL_SECONDS}s, "
          f"max {config.MAX_POSTS_PER_DAY} posts/day\n")

    state = load_state()

    # First run: mark everything currently in the feeds as seen so the bot
    # doesn't flood the timeline with backlog on startup.
    if not state["seen"]:
        state["seen"] = [i.id for i in sources.fetch_all()]
        save_state(state)
        print(f"First run: baselined {len(state['seen'])} existing items. "
              "Only news from now on will be tweeted.\n")

    while True:
        try:
            run_cycle(state)
        except KeyboardInterrupt:
            print("\nStopping.")
            break
        except Exception as e:
            print(f"Cycle error (continuing): {e}")
        time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()
