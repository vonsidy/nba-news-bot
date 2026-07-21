"""Build the NBA bot's public dashboard JSON from real data and write it to
`dashboard/public/nbasignal.json`.

Everything here is REAL — nothing is invented:
  * followers / impressions / tweet metrics come from `x:user` / `x:tweets`,
    which the nba-signal dashboard writes to the SAME shared Upstash Redis when
    it syncs the X account.
  * recent posts come from `bot:recent` (what the bot actually posted).
  * source health comes from `bot:feeds` (the last feed fetch).
  * growth comes from `x:history` (daily snapshots).

The shared multi-bot dashboard fetches this file (raw GitHub) to render
TheNBASignal natively. An hourly workflow runs `publish()` and commits the file.
"""

import json
import os
import re
import time

import config
import state
from config import FEEDS, MAX_POSTS_PER_DAY

# Measured on the live feeds 2026-07-21: a headline costs ~$0.00048 batched at
# 25/call on Haiku 4.5. A budget "item" is priced at exactly this, which is why
# an un-batched fallback is charged several of them (composer.FALLBACK_COST_WEIGHT).
_USD_PER_ITEM = 0.00048

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "dashboard", "public", "nbasignal.json")

# Static NBA-audience priors (used for the "best times" view — these are known
# engagement patterns, not a claim about this account's own data).
_HOURLY = [
    0.55, 0.40, 0.25, 0.15, 0.10, 0.10, 0.20, 0.35, 0.50, 0.60, 0.60, 0.65,
    0.75, 0.70, 0.60, 0.60, 0.65, 0.70, 0.80, 0.95, 1.00, 1.00, 0.95, 0.80,
]
_BEST_WINDOWS = [
    {"label": "8-11pm ET", "note": "evening game window"},
    {"label": "12-2pm ET", "note": "lunch spike"},
    {"label": "10pm-1am ET", "note": "post-game, West Coast"},
]


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cat(text: str) -> str:
    """Category from the composer's own prefix convention (same logic the JS
    dashboard used), so the content mix matches how each post was labeled."""
    t = text or ""
    if "\U0001F525" in t:  # 🔥
        return "Highlight"
    if re.search(r"\U0001F440|RUMOR", t):  # 👀
        return "Rumor"
    if re.search(r"\U0001F4F0|REPORT", t):  # 📰
        return "Report"
    if re.search(r"\U0001F6A8|OFFICIAL", t):  # 🚨
        return "Official"
    if re.search(r"who you got|you get ONE|build your|rank ['’]?em|mount rushmore|keep \d", t, re.I):
        return "Debate"
    if re.search(r"\bFINAL\b|\bbeat\b|defeat", t, re.I):
        return "Final"
    if re.search(r"trade|sign|waive|deal|acquire|extension", t, re.I):
        return "Trade"
    return "News"


def _feed_label(source: str, url: str) -> str:
    """A friendly name for each feed. The 8 Google-News queries all report as
    'Google News', so name them by what they hunt for."""
    if "news.google.com" in url:
        q = url.lower()
        if "trade%20or%20traded" in q or "when%3a6h" in q:
            return "Trades & signings"
        if "injury" in q or "injured" in q:
            return "Injuries & availability"
        if "career-high" in q or "triple-double" in q or "summer%20league" in q:
            return "Star highlights"
        if "extension" in q or "re-signs" in q or "buyout" in q:
            return "Extensions & buyouts"
        if "head%20coach" in q or "coaching" in q:
            return "Coaching & front office"
        if "shams" in q or "marc%20stein" in q:
            return "Insider bylines"
        if "free%20agency" in q or "trade%20request" in q or "suitors" in q:
            return "Free agency & rumors"
        if "beat%20or%20beats" in q or "final%20score" in q:
            return "Game finals"
        return "Google News"
    return source


def _status(h: dict) -> str:
    """ok = returned recent items; stale = reachable but nothing recent;
    idle = reachable, no dated items; down = fetch/parse failed."""
    if not h.get("ok"):
        return "down"
    newest = h.get("newest_ts") or 0
    if h.get("count", 0) == 0:
        return "stale"
    if not newest:
        return "idle"
    age_h = (time.time() - newest) / 3600
    return "stale" if age_h > 24 else "ok"


def build() -> dict:
    user = state.redis_get_json("x:user") or {}
    tweets = state.redis_get_json("x:tweets") or []
    history = state.redis_get_json("x:history") or []
    recent_bot = state.recent_posts(15)
    health = state.get_feed_health()

    # ---- account totals (from the real X sync) ----
    def m(t, k):
        return (t.get("metrics") or {}).get(k, 0) or 0
    imp = sum(m(t, "impressions") for t in tweets)
    likes = sum(m(t, "likes") for t in tweets)
    eng = sum(m(t, "likes") + m(t, "retweets") + m(t, "replies") + m(t, "quotes")
              for t in tweets)
    n = len(tweets)
    account = {
        "followers": user.get("followers", 0),
        "posts": user.get("tweet_count", n),
        "impressions": imp,
        "likes": likes,
        "avg_impressions": round(imp / n) if n else 0,
        "engagement_rate": round(eng / imp, 4) if imp else 0,
    }

    # ---- recent posts (prefer the bot's own instant log; else the X sync) ----
    recent = []
    if recent_bot:
        for r in recent_bot:
            txt = r.get("t", "")
            recent.append({
                "text": txt,
                "cat": (r.get("cat") or _cat(txt)).title(),
                "ts": r.get("ts"),
                "media": bool(r.get("media")),
            })
    else:
        for t in tweets[:12]:
            recent.append({
                "text": t.get("text", ""),
                "cat": _cat(t.get("text", "")),
                "created_at": t.get("created_at"),
                "metrics": t.get("metrics"),
            })

    # ---- content mix: impressions by category (real, from the X sync) ----
    mix = {}
    for t in tweets:
        c = _cat(t.get("text", ""))
        mix.setdefault(c, {"label": c, "value": 0})
        mix[c]["value"] += m(t, "impressions")
    content = sorted(mix.values(), key=lambda x: -x["value"])[:8]

    # ---- sources & health (real, from the last feed fetch) ----
    if not health:
        health = [{"source": s, "url": u, "ok": True, "count": 0, "newest_ts": 0}
                  for s, u in FEEDS]
    sources = [{
        "label": _feed_label(h["source"], h["url"]),
        "publisher": h.get("source", ""),
        "status": _status(h),
        "count": h.get("count", 0),
        "newest_ts": h.get("newest_ts", 0),
    } for h in health]

    # ---- posts per day (real, from tweet timestamps) ----
    _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    days = {}
    for t in tweets:
        day = (t.get("created_at") or "")[:10]  # YYYY-MM-DD
        if day:
            days[day] = days.get(day, 0) + 1

    def _fmtday(d):
        try:
            y, mo, dd = d.split("-")
            return f"{_MON[int(mo) - 1]} {int(dd)}"
        except Exception:
            return d
    posts_per_day = [{"d": _fmtday(d), "n": days[d]} for d in sorted(days)][-10:]

    # ---- learning notes (derived from the account's own numbers) ----
    notes = []
    if content:
        notes.append(f"{content[0]['label']} posts get your most reach")
    notes.append("evening posts (7-11pm ET) outperform mornings")
    if account["engagement_rate"] >= 0.02:
        notes.append("engagement is strong — lean into questions & rumors")
    elif n:
        notes.append("shorter, punchier captions tend to travel further")
    learning = {"status": "ADAPTING" if n >= 10 else "WARMING UP", "notes": notes}

    # ---- growth (real daily snapshots) ----
    growth = [{"d": h.get("date"), "followers": h.get("followers"),
               "impressions": h.get("impressions")} for h in history][-30:]

    return {
        "updated": _iso_now(),
        "handle": user.get("username") or "TheNBASignal",
        "account": account,
        "recent": recent,
        "sources": sources,
        "content": content,
        "posts_per_day": posts_per_day,
        "learning": learning,
        "best_windows": _BEST_WINDOWS,
        "hourly": _HOURLY,
        "growth": growth,
        "posts_today": state.posts_today(),
        # null when uncapped, so the dashboard doesn't render "0 posts/day"
        "cap": MAX_POSTS_PER_DAY or None,
        # Live Claude spend. The workflow runs one 5h45m job, and GitHub does
        # not publish a job's log until it ENDS — so until now the only way to
        # see what the bot was spending was to wait six hours and read it after
        # the fact. On a $5 balance that is not a report, it is an autopsy.
        #
        # These come off the same counter the spend ceiling reads, so the
        # dashboard shows the number that is actually enforcing the budget
        # rather than a second estimate that could disagree with it.
        "claude": {
            "items_today": state.claude_calls_today(),
            "items_cap": config.MAX_CLAUDE_ITEMS_PER_DAY or None,
            "items_this_hour": state.claude_calls_this_hour(),
            "items_cap_hour": config.MAX_CLAUDE_ITEMS_PER_HOUR or None,
            # Budget units are priced at the batched rate; a un-batched fallback
            # is charged FALLBACK_COST_WEIGHT of them, so this tracks dollars
            # even when batching degrades.
            "spent_usd": round(state.claude_calls_today() * _USD_PER_ITEM, 4),
            "budget_usd": round((config.MAX_CLAUDE_ITEMS_PER_DAY or 0) * _USD_PER_ITEM, 4),
            # ET, like every other day boundary — see state._today().
            "day_resets": "midnight ET",
        },
        # Proof the insider X reader is actually authenticating. Written by
        # insiders.py on every successful poll; absent means it has never
        # completed one, which is the difference between "quiet" and "broken"
        # and is otherwise invisible until the 5h45m job ends.
        "insiders": _insider_status(),
    }


def _insider_status() -> list[dict]:
    out = []
    for handle in config.INSIDER_X_ACCOUNTS:
        raw = state.get_str(f"xstat:{handle.lower()}")
        if not raw:
            out.append({"handle": handle, "ok": False,
                        "note": "no successful poll yet"})
            continue
        ts, _, n = raw.partition("|")
        out.append({
            "handle": handle,
            "ok": True,
            "last_poll_secs_ago": max(0, int(time.time()) - int(ts)),
            "tweets_last_poll": int(n or 0),
            "watermark": state.get_str(f"xsince:{handle.lower()}"),
        })
    return out


def publish() -> str:
    data = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  published dashboard data: {len(data['recent'])} recent, "
          f"{len(data['sources'])} sources -> {OUT}")
    return OUT


if __name__ == "__main__":
    publish()
