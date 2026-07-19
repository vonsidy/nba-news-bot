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
import photos
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

    # Freshness by type: a trade/signing is still worth posting hours later, but
    # any other news (a performance, a quote) is stale within the tight window.
    # We only know which it is after Claude classifies it, so enforce it here.
    if item.published_ts:
        age_min = (time.time() - item.published_ts) / 60
        max_age = config.TRADE_MAX_AGE_MIN if result.get("is_trade") else config.FRESH_MAX_AGE_MIN
        if age_min > max_age:
            print(f"  too stale ({int(age_min)}m old) for a non-trade, skipping: {item.title[:60]}")
            return

    text = result["tweet"].strip()
    if item.link:
        text = f"{text}\n{item.link}"

    # Auto-generate a TRADE ALERT graphic when the item is a player move.
    # Try a reuse-licensed (CC / public-domain) player photo from Wikimedia;
    # fall back to the photo-free design card when none exists.
    image = None
    if result.get("is_trade") and result.get("player") and result.get("to_team"):
        photo, credit = None, None
        res = photos.get_player_photo(result["player"])
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
