"""Read breaking news straight from insider X accounts.

The Google News insider feed catches a Shams scoop only after some outlet has
written it up and Google has indexed the article — 5 to 20 minutes behind the
tweet. This reads the tweet itself, so the same scoop is in hand in ~60s.

COST MODEL — read this before changing anything here.

X bills third-party reads at $0.005 per RESOURCE RETURNED. Not per request. So
the bill is set by how many tweets come back, and the one job of this module is
to make sure a tweet comes back exactly once, ever.

`since_id` is how. Every poll asks only for tweets NEWER than the newest one
already seen, so an idle poll returns zero resources and costs zero. Polling
every 60s on a quiet afternoon is free.

X is also understood to deduplicate billing for the same resource within a
24-hour window, which would make repeat fetches free on its own. This module
does NOT rely on that. If that dedup is ever wrong, absent, or changed, a
naive poller doing 1,440 requests a day at 5 tweets each would bill 7,200
reads — $36/day. `since_id` removes the assumption entirely: we never ask for
the same tweet twice, so there is nothing to deduplicate.
"""

import re
import time

import tweepy

import config
import state
from sources import NewsItem

_client = None

# Per-process counters, printed at the end of a cycle alongside the Claude ones
# so both meters are visible in the same place.
USAGE = {"requests": 0, "tweets": 0, "errors": 0}


def _get_client() -> tweepy.Client:
    """A bearer-token client — app-only auth is what /2/users/:id/tweets wants."""
    global _client
    if _client is None:
        _client = tweepy.Client(
            bearer_token=config.X_BEARER_TOKEN or None,
            consumer_key=config.X_API_KEY,
            consumer_secret=config.X_API_SECRET,
            access_token=config.X_ACCESS_TOKEN,
            access_token_secret=config.X_ACCESS_SECRET,
        )
    return _client


# Strip the trailing link X appends to its own tweets, plus any url in the body.
# Nothing with a url may reach the composer: X prices a post containing one at
# $0.200 against $0.015, and the surest way to never pay that is for the text to
# never contain a link in the first place.
_URL = re.compile(r"(?i)\bhttps?://\S+")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _URL.sub("", text or "")).strip()


def _resolve_user_id(handle: str) -> str | None:
    """Handle -> numeric id, cached forever in state.

    Cached because the lookup is itself a billed read. An account's id never
    changes (the handle can), so this is a one-time $0.005 per account rather
    than one per cycle.
    """
    key = f"xuid:{handle.lower()}"
    cached = state.get_str(key)
    if cached:
        return cached
    try:
        USAGE["requests"] += 1
        resp = _get_client().get_user(username=handle)
        uid = str(resp.data.id) if resp and resp.data else None
    except Exception as e:
        USAGE["errors"] += 1
        print(f"  X: could not resolve @{handle}: {str(e)[:90]}")
        return None
    if uid:
        state.set_str(key, uid)
        print(f"  X: resolved @{handle} -> {uid} (cached, not billed again)")
    return uid


def fetch_insider_items() -> list[NewsItem]:
    """New tweets from the configured insider accounts, as NewsItems.

    Returns [] and costs nothing when disabled, unconfigured, or when nobody
    has posted since the last poll.
    """
    if not config.INSIDER_X_ENABLED or not config.INSIDER_X_ACCOUNTS:
        return []

    items: list[NewsItem] = []
    for handle in config.INSIDER_X_ACCOUNTS:
        uid = _resolve_user_id(handle)
        if not uid:
            continue
        since_key = f"xsince:{handle.lower()}"
        since_id = state.get_str(since_key)

        params = {
            "id": uid,
            # Originals only. Shams replies and retweets constantly, and every
            # one of those is a billed resource — this flag is the difference
            # between ~$2 and ~$10 a month.
            "exclude": ["retweets", "replies"],
            # Hard ceiling per poll so a thread-storm cannot spike the bill.
            "max_results": max(5, min(config.INSIDER_X_MAX_PER_POLL, 100)),
            "tweet_fields": ["created_at"],
        }
        if since_id:
            params["since_id"] = since_id
        else:
            # FIRST EVER POLL for this account. Without since_id the API returns
            # max_results tweets — all of them old news, all of them billed, and
            # posting them would flood the timeline with yesterday's scoops.
            # Take the smallest page X allows purely to establish a watermark.
            params["max_results"] = 5

        try:
            USAGE["requests"] += 1
            resp = _get_client().get_users_tweets(**params)
        except tweepy.TooManyRequests:
            USAGE["errors"] += 1
            print(f"  X: rate limited on @{handle}, skipping this cycle")
            continue
        except Exception as e:
            USAGE["errors"] += 1
            print(f"  X: read failed for @{handle}: {str(e)[:90]}")
            continue

        tweets = list(resp.data or [])
        USAGE["tweets"] += len(tweets)
        # Leave a breadcrumb the DASHBOARD can read. USAGE above is per-process
        # and the bot runs one 5h45m job, so without this the only proof the
        # reader authenticated at all is a log nobody can see until the job
        # ends. "Working but unverifiable for six hours" is not working.
        state.set_str(f"xstat:{handle.lower()}",
                      f"{int(time.time())}|{len(tweets)}")
        if not tweets:
            continue

        # Advance the watermark to the newest id regardless of what survives
        # filtering below — the point is never to be billed for these again.
        newest = max(str(t.id) for t in tweets)
        state.set_str(since_key, newest)

        if not since_id:
            print(f"  X: baselined @{handle} at {newest} "
                  f"({len(tweets)} tweet(s) read, none posted — first run)")
            continue

        for t in tweets:
            text = _clean(t.text)
            if not text:
                continue
            items.append(NewsItem(
                id=f"x:{t.id}",
                # Attribution the composer can quote directly. It is also the
                # literal truth of where this came from, unlike an aggregator.
                source=f"@{handle}",
                title=text,
                summary="",
                # Deliberately empty. A tweet url in the body costs 13x.
                link="",
                published_ts=t.created_at.timestamp() if t.created_at else 0.0,
            ))
        print(f"  X: {len(tweets)} new tweet(s) from @{handle} "
              f"(~${len(tweets) * 0.005:.3f})")

    return items


def usage_line() -> str:
    u = USAGE
    return (f"X reads this cycle: {u['requests']} request(s), {u['tweets']} tweet(s) "
            f"(~${u['tweets'] * 0.005:.3f}), {u['errors']} error(s)")
