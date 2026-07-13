"""NBA breaking-news bot: watch RSS feeds, compose tweets with Claude,
generate a graphic for trades, and post to X.

Run modes:
  python bot.py          # continuous loop (local dev / always-on host)
  python bot.py --once   # single pass, then exit (used by GitHub Actions cron)
"""

import sys
import time

import card
import config
import sources
import state
import tweeter
from composer import compose

# Windows consoles default to cp1252, which crashes on emoji in headlines/tweets
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def process_item(item: sources.NewsItem) -> None:
    # Mark seen first — a bad item shouldn't be retried forever
    state.mark_seen(item.id)

    if state.posts_today() >= config.MAX_POSTS_PER_DAY:
        print("Daily post cap reached; skipping remaining items")
        return

    result = compose(item)
    if not result or not result.get("newsworthy") or not result.get("tweet"):
        print(f"  skipped: {item.title[:70]}")
        return

    text = result["tweet"].strip()
    if item.link:
        text = f"{text}\n{item.link}"

    # Auto-generate a TRADE ALERT graphic when the item is a player move
    image = None
    if result.get("is_trade") and result.get("player") and result.get("to_team"):
        image = card.make_trade_card(
            player=result["player"],
            to_team=result["to_team"],
            from_team=result.get("from_team") or None,
        )
        if image:
            print(f"  generated trade card: {result['player']} -> {result['to_team']}")

    if tweeter.post(text, image=image):
        state.incr_posts()


def run_cycle() -> None:
    items = [
        i for i in sources.fetch_all()
        if not state.is_seen(i.id) and sources.is_fresh(i)
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
