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


def _upload_media_v2(image: bytes) -> str | None:
    """Upload a PNG via POST /2/media/upload, signed with the same OAuth 1.0
    credentials the bot posts with. Returns the media id, or None.

    Exists because tweepy only speaks the v1.1 upload host. Kept deliberately
    dependency-free beyond requests + requests-oauthlib, both of which tweepy
    already pulls in, so this adds nothing to requirements.txt."""
    try:
        import requests
        from requests_oauthlib import OAuth1
    except ImportError as e:
        print(f"  v2 upload unavailable ({e})")
        return None

    auth = OAuth1(config.X_API_KEY, config.X_API_SECRET,
                  config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET)
    try:
        r = requests.post(
            "https://api.x.com/2/media/upload",
            auth=auth,
            files={"media": ("card.png", image, "image/png")},
            data={"media_category": "tweet_image"},
            timeout=30,
        )
    except Exception as e:
        print(f"  v2 upload request error: {e}")
        return None
    if r.status_code >= 400:
        print(f"  v2 upload HTTP {r.status_code}: {r.text[:200]}")
        return None
    try:
        j = r.json()
    except ValueError:
        print("  v2 upload returned non-JSON")
        return None
    # v2 nests it under data.id; older shapes used media_id_string at the root.
    return (j.get("data") or {}).get("id") or j.get("media_id_string") or j.get("id")


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
        # tweepy's media_upload targets upload.twitter.com/1.1, and X has been
        # retiring v1.1 for pay-per-use tiers — posting via v2 (create_tweet)
        # keeps working while the upload 403s, which shows up as cards being
        # generated and then silently never appearing on the timeline. Try
        # v1.1, fall back to POST /2/media/upload with the same OAuth 1.0
        # credentials, and only then give up.
        try:
            media = _get_api_v1().media_upload(
                filename="trade.png", file=io.BytesIO(image)
            )
            media_ids = [media.media_id]
        except tweepy.TweepyException as e:
            print(f"  v1.1 media upload failed ({e}); trying v2")
            mid = _upload_media_v2(image)
            media_ids = [mid] if mid else None
            if mid:
                print("  v2 media upload succeeded")
            else:
                print("  v2 media upload also failed; posting text-only")

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
