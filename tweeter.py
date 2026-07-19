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


def creds_report() -> str:
    """Report the shape of the X credentials WITHOUT revealing them — just
    lengths and format, so a 401 can be traced to the specific malformed key.
    Safe to print in public logs. Expected: API key ~25 chars, API secret ~50,
    access token ~50 with a leading '<digits>-' , access secret ~45."""
    at = config.X_ACCESS_TOKEN or ""
    return (
        "X creds shape — "
        f"API_KEY:{len(config.X_API_KEY or '')} "
        f"API_SECRET:{len(config.X_API_SECRET or '')} "
        f"ACCESS_TOKEN:{len(at)}(dash={'-' in at},startsdigit={at[:1].isdigit()}) "
        f"ACCESS_SECRET:{len(config.X_ACCESS_SECRET or '')}  "
        "[expect API_KEY~25 API_SECRET~50 ACCESS_TOKEN~50+dash ACCESS_SECRET~45]"
    )


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

    media_ids = None
    if image:
        # Image upload uses the v1.1 endpoint. If it fails (e.g. API access
        # tier), don't lose the whole story — fall back to a text-only tweet.
        try:
            media = _get_api_v1().media_upload(
                filename="trade.png", file=io.BytesIO(image)
            )
            media_ids = [media.media_id]
        except tweepy.TweepyException as e:
            print(f"  image upload failed ({e}); posting text-only")
            media_ids = None

    try:
        _get_client().create_tweet(text=text, media_ids=media_ids)
        print(f"\nPosted ({len(text)} chars){' + image' if media_ids else ''}:\n{text}\n")
        return True
    except tweepy.TooManyRequests:
        print("X API rate limited — will retry items next cycle")
        return False
    except tweepy.TweepyException as e:
        print(f"tweet create failed: {e}")
        return False
