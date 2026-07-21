"""Cross-run state: dedup memory + daily post counter.

Uses Upstash Redis (shared with the dashboard) when configured, so state
survives across serverless / GitHub Actions runs. Falls back to a local
JSON file for local development when Redis env vars are absent.
"""

import json
import os
import time
from datetime import date, timedelta, timezone, datetime

import config

_MAX_SEEN = 3000

try:
    from upstash_redis import Redis
    _redis = (
        Redis(url=config.UPSTASH_URL, token=config.UPSTASH_TOKEN)
        if config.UPSTASH_URL and config.UPSTASH_TOKEN
        else None
    )
except ImportError:
    _redis = None

_SEEN_KEY = "bot:seen"
_RECENT_KEY = "bot:recent"   # last posts the bot made (for the dashboard's "recent posts")
_FEEDS_KEY = "bot:feeds"     # last feed-health snapshot (for the dashboard's "sources")
_LOCAL = config.STATE_FILE


def _today() -> str:
    """Today's date in US Eastern — the boundary every daily counter turns on.

    This was UTC, which put the reset at 8pm ET (EDT). That is the worst
    possible moment: it lands in the middle of NBA evening news, so a busy
    night was split across two budget days, one player could be posted about
    twice in a single evening, and the owner — who works in ET — could not
    reconcile a balance against a "day" that ended at dinner time.

    ET matches both the news cycle and the person reading the numbers.
    bot._et_hour() already picked America/New_York for the same reason.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        # No tzdata (bare containers): EDT-ish is far closer than UTC.
        return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def today_key() -> str:
    """Public ET date string, used to scope per-day dedup keys."""
    return _today()


def _post_key() -> str:
    return f"bot:posts:{_today()}"


def _hl_key() -> str:
    return f"bot:highlights:{_today()}"


# ---- Redis-backed implementation ----

def _redis_is_seen(item_id: str) -> bool:
    return bool(_redis.sismember(_SEEN_KEY, item_id))


def _redis_mark_seen(item_id: str) -> None:
    _redis.sadd(_SEEN_KEY, item_id)


def _redis_posts_today() -> int:
    val = _redis.get(_post_key())
    return int(val) if val else 0


def _redis_incr_posts() -> None:
    key = _post_key()
    n = _redis.incr(key)
    if n == 1:
        _redis.expire(key, 60 * 60 * 30)  # auto-clean after ~30h


def _redis_highlights_today() -> int:
    val = _redis.get(_hl_key())
    return int(val) if val else 0


def _redis_incr_highlights() -> None:
    key = _hl_key()
    n = _redis.incr(key)
    if n == 1:
        _redis.expire(key, 60 * 60 * 30)


# ---- Local JSON fallback ----

def _local_load() -> dict:
    if os.path.exists(_LOCAL):
        try:
            with open(_LOCAL, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen": [], "post_day": "", "post_count": 0, "hl_count": 0}


def _local_save(d: dict) -> None:
    d["seen"] = d["seen"][-_MAX_SEEN:]
    with open(_LOCAL, "w", encoding="utf-8") as f:
        json.dump(d, f)


# ---- Public API (dispatches to Redis or local) ----

def is_seen(item_id: str) -> bool:
    if _redis:
        return _redis_is_seen(item_id)
    return item_id in set(_local_load()["seen"])


def mark_seen(item_id: str) -> None:
    if _redis:
        _redis_mark_seen(item_id)
    else:
        d = _local_load()
        d["seen"].append(item_id)
        _local_save(d)


def has_any_seen() -> bool:
    if _redis:
        return bool(_redis.scard(_SEEN_KEY))
    return bool(_local_load()["seen"])


def posts_today() -> int:
    if _redis:
        return _redis_posts_today()
    d = _local_load()
    return d["post_count"] if d.get("post_day") == _today() else 0


def incr_posts() -> None:
    if _redis:
        _redis_incr_posts()
    else:
        d = _local_load()
        if d.get("post_day") != _today():
            d["post_day"], d["post_count"], d["hl_count"] = _today(), 0, 0
        d["post_count"] += 1
        _local_save(d)


def _player_key_name(pkey: str) -> str:
    return f"bot:player:{_today()}:{pkey}"


def player_posts_today(pkey: str) -> int:
    """How many items naming this player as the primary subject posted today.
    Backstop against one story dominating the feed when the semantic dedup
    misses a phrasing — see bot.process_item."""
    if _redis:
        val = _redis.get(_player_key_name(pkey))
        return int(val) if val else 0
    d = _local_load()
    return (d.get("players") or {}).get(_player_key_name(pkey), 0)


def _subject_names_key() -> str:
    return f"bot:subject_names:{_today()}"


def posted_names_today() -> set[str]:
    """Raw player names posted today, e.g. {"LeBron James", "Jett Howard"}.

    Kept alongside the hashed per-player counters because those are one-way:
    you can ask "have I posted about key X" but you cannot ask "which players
    have I posted about", and the pre-compose check needs the second question
    so it can scan a headline before paying to understand it."""
    if _redis:
        vals = _redis.smembers(_subject_names_key())
        return {str(v) for v in (vals or [])}
    d = _local_load()
    return set((d.get("subject_names") or {}).get(_today(), []))


def record_posted_name(name: str) -> None:
    if not name:
        return
    if _redis:
        key = _subject_names_key()
        _redis.sadd(key, name)
        _redis.expire(key, 60 * 60 * 30)  # auto-clean after ~30h
        return
    d = _local_load()
    names = d.get("subject_names") or {}
    today = names.get(_today(), [])
    if name not in today:
        today.append(name)
    d["subject_names"] = {_today(): today}  # keep only today
    _local_save(d)


def _claude_hour_key() -> str:
    return f"bot:claude_calls_hour:{time.strftime('%Y-%m-%dT%H', time.gmtime())}"


def claude_calls_this_hour() -> int:
    """Calls made in the current UTC hour. Paired with the daily cap so the
    day's budget is spread across the day instead of spent in the first hour."""
    if _redis:
        val = _redis.get(_claude_hour_key())
        return int(val) if val else 0
    d = _local_load()
    return (d.get("claude_calls_hour") or {}).get(_claude_hour_key(), 0)


def incr_claude_calls_hour(n: int = 1) -> None:
    """Charge `n` items against this hour's allowance.

    Takes a count because composing is batched: one HTTP request now carries
    many items, and the budget is denominated in ITEMS (what actually costs
    money), not requests."""
    if _redis:
        key = _claude_hour_key()
        total = _redis.incrby(key, n)
        if total == n:
            _redis.expire(key, 3 * 3600)
        return
    d = _local_load()
    h = _claude_hour_key()
    d["claude_calls_hour"] = {h: (d.get("claude_calls_hour") or {}).get(h, 0) + n}
    _local_save(d)


def _claude_calls_key() -> str:
    return f"bot:claude_calls:{_today()}"


def claude_calls_today() -> int:
    """How many Claude compose calls have been made today (UTC).

    Every call costs money whether or not the item ends up posted, so this
    counts ATTEMPTS, not posts — the dedup and freshness checks that run after
    compose() discard a meaningful share of them."""
    if _redis:
        val = _redis.get(_claude_calls_key())
        return int(val) if val else 0
    d = _local_load()
    return (d.get("claude_calls") or {}).get(_today(), 0)


def incr_claude_calls(n: int = 1) -> None:
    """Charge `n` items against today's budget. See incr_claude_calls_hour."""
    if _redis:
        key = _claude_calls_key()
        total = _redis.incrby(key, n)
        if total == n:
            _redis.expire(key, 60 * 60 * 30)  # auto-clean after ~30h
        return
    d = _local_load()
    calls = d.get("claude_calls") or {}
    calls[_today()] = calls.get(_today(), 0) + n
    d["claude_calls"] = {_today(): calls[_today()]}  # keep only today
    _local_save(d)


def incr_player_posts(pkey: str) -> None:
    if _redis:
        key = _player_key_name(pkey)
        n = _redis.incr(key)
        if n == 1:
            _redis.expire(key, 60 * 60 * 30)  # auto-clean after ~30h
        return
    d = _local_load()
    players = d.get("players") or {}
    k = _player_key_name(pkey)
    players[k] = players.get(k, 0) + 1
    d["players"] = players
    _local_save(d)


def highlights_today() -> int:
    if _redis:
        return _redis_highlights_today()
    d = _local_load()
    return d.get("hl_count", 0) if d.get("post_day") == _today() else 0


def incr_highlights() -> None:
    if _redis:
        _redis_incr_highlights()
    else:
        d = _local_load()
        if d.get("post_day") != _today():
            d["post_day"], d["post_count"], d["hl_count"] = _today(), 0, 0
        d["hl_count"] = d.get("hl_count", 0) + 1
        _local_save(d)


def get_flag(key: str) -> bool:
    """True if a self-expiring flag is currently set. Used for trade dedup that
    must persist for DAYS (not reset at midnight) so a confirmed deal posts once
    and never resurfaces from follow-up articles."""
    if _redis:
        return bool(_redis.get(key))
    d = _local_load()
    exp = (d.get("flags") or {}).get(key)
    return bool(exp and exp > time.time())


def set_flag(key: str, ttl_seconds: int) -> None:
    """Set a flag that auto-expires after ttl_seconds (so old keys clean up and
    the same teams can trade again later)."""
    if _redis:
        _redis.set(key, "1", ex=ttl_seconds)
        return
    d = _local_load()
    now = time.time()
    flags = {k: v for k, v in (d.get("flags") or {}).items() if v > now}
    flags[key] = now + ttl_seconds
    d["flags"] = flags
    _local_save(d)


def record_post(text: str, category: str = "", has_media: bool = False) -> None:
    """Remember a post the bot just made, so the dashboard can show a live
    'most recent posts' list. Keeps the newest ~20; oldest fall off."""
    entry = {"t": text, "ts": time.time(), "cat": category or "", "media": bool(has_media)}
    if _redis:
        try:
            _redis.lpush(_RECENT_KEY, json.dumps(entry))
            _redis.ltrim(_RECENT_KEY, 0, 19)
        except Exception:
            pass
    else:
        d = _local_load()
        r = d.get("recent") or []
        r.insert(0, entry)
        d["recent"] = r[:20]
        _local_save(d)


def recent_posts(n: int = 15) -> list:
    """The most recent posts the bot made (newest first)."""
    if _redis:
        try:
            raw = _redis.lrange(_RECENT_KEY, 0, n - 1) or []
        except Exception:
            return []
        out = []
        for x in raw:
            try:
                out.append(json.loads(x) if isinstance(x, str) else x)
            except Exception:
                pass
        return out
    return (_local_load().get("recent") or [])[:n]


def set_feed_health(feeds: list) -> None:
    """Store the latest per-feed health snapshot (list of dicts) for the dashboard."""
    if _redis:
        try:
            _redis.set(_FEEDS_KEY, json.dumps(feeds))
        except Exception:
            pass
    else:
        d = _local_load()
        d["feeds"] = feeds
        _local_save(d)


def get_feed_health() -> list:
    if _redis:
        try:
            s = _redis.get(_FEEDS_KEY)
            return json.loads(s) if s else []
        except Exception:
            return []
    return _local_load().get("feeds") or []


def redis_get_json(key: str):
    """Read a JSON value the dashboard wrote to the SAME shared Redis
    (x:user / x:tweets / x:history). Returns None when absent or Redis is off."""
    if not _redis:
        return None
    try:
        v = _redis.get(key)
    except Exception:
        return None
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


def backend() -> str:
    return "Upstash Redis" if _redis else "local file"
