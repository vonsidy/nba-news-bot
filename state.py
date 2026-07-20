"""Cross-run state: dedup memory + daily post counter.

Uses Upstash Redis (shared with the dashboard) when configured, so state
survives across serverless / GitHub Actions runs. Falls back to a local
JSON file for local development when Redis env vars are absent.
"""

import json
import os
from datetime import date, timezone, datetime

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
_SIGNALS_KEY = "bot:signals"
_LOCAL = config.STATE_FILE


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def today_key() -> str:
    """Public UTC date string, used to scope per-day dedup keys."""
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
    return {"seen": [], "post_day": "", "post_count": 0, "hl_count": 0,
            "signals": []}


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


def record_signal(signal: dict) -> None:
    """Store post-pipeline telemetry after a post is already live.

    This call is deliberately made after tweeter.post() succeeds, so telemetry
    can never delay breaking news. Keep only a small rolling window.
    """
    item = dict(signal)
    if _redis:
        history = _redis.get(_SIGNALS_KEY) or []
        if not isinstance(history, list):
            history = []
        history.insert(0, item)
        _redis.set(_SIGNALS_KEY, history[:200])
        return
    d = _local_load()
    d.setdefault("signals", []).insert(0, item)
    d["signals"] = d["signals"][:200]
    _local_save(d)


def backend() -> str:
    return "Upstash Redis" if _redis else "local file"
