"""Post tweets to X, with optional image. In DRY_RUN mode, print instead."""

import io

import tweepy

import config

_client = None  # v2 Client for creating tweets
_api_v1 = None  # v1.1 API, needed only for media upload


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


def _get_api_v1() -> tweepy.API:
    global _api_v1
    if _api_v1 is None:
        auth = tweepy.OAuth1UserHandler(
            config.X_API_KEY, config.X_API_SECRET,
            config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET,
        )
        _api_v1 = tweepy.API(auth)
    return _api_v1


def post(text: str, image: bytes | None = None) -> bool:
    """Post a tweet, optionally with a PNG image. Returns True on success
    (or in dry-run mode)."""
    if len(text) > 280:
        text = text[:277] + "..."

    if config.DRY_RUN:
        tag = f" + image ({len(image)} bytes)" if image else ""
        print(f"\n[DRY RUN] Would tweet ({len(text)} chars){tag}:\n{text}\n")
        if image:
            with open("sample_card.png", "wb") as f:
                f.write(image)
            print("[DRY RUN] wrote the graphic to sample_card.png\n")
        return True

    try:
        media_ids = None
        if image:
            media = _get_api_v1().media_upload(
                filename="trade.png", file=io.BytesIO(image)
            )
            media_ids = [media.media_id]
        _get_client().create_tweet(text=text, media_ids=media_ids)
        print(f"\nPosted ({len(text)} chars){' + image' if image else ''}:\n{text}\n")
        return True
    except tweepy.TooManyRequests:
        print("X API rate limited — will retry items next cycle")
        return False
    except tweepy.TweepyException as e:
        print(f"X API error: {e}")
        return False
