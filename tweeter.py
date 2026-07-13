"""Post tweets to X. In DRY_RUN mode, print instead."""

import tweepy

import config

_client = None


def _get_client() -> tweepy.Client:
    global _client
    if _client is None:
        _client = tweepy.Client(
            consumer_key=config.X_API_KEY,
            consumer_secret=config.X_API_SECRET,
            access_token=config.X_ACCESS_TOKEN,
            access_token_secret=config.X_ACCESS_SECRET,
        )
    return _client


def post(text: str) -> bool:
    """Post a tweet. Returns True on success (or in dry-run mode)."""
    if len(text) > 280:
        text = text[:277] + "..."

    if config.DRY_RUN:
        print(f"\n[DRY RUN] Would tweet ({len(text)} chars):\n{text}\n")
        return True

    try:
        _get_client().create_tweet(text=text)
        print(f"\nPosted ({len(text)} chars):\n{text}\n")
        return True
    except tweepy.TooManyRequests:
        print("X API rate limited — will retry items next cycle")
        return False
    except tweepy.TweepyException as e:
        print(f"X API error: {e}")
        return False
