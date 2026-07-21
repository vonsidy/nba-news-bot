"""Fetch and normalize NBA news items from RSS feeds."""

import calendar
import concurrent.futures
import re
import time
from dataclasses import dataclass

import feedparser

from config import FEEDS, FRESH_MAX_AGE_MIN


@dataclass
class NewsItem:
    id: str
    source: str
    title: str
    summary: str
    link: str
    published_ts: float  # unix epoch; 0 if the feed gave no date


def _entry_id(entry) -> str:
    return entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")


def _entry_ts(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(parsed) if parsed else 0.0


def _clean(text: str) -> str:
    """Strip HTML tags/whitespace so the model gets a clean headline/summary."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


# Google News hands back some publishers as a bare domain ("roundtable.io",
# "nba.com") rather than a name. That matters for cost, not just for looks: X
# auto-links anything that parses as a domain, and a post containing a url
# bills at $0.200 against $0.015 for one without. So an attribution reading
# "per roundtable.io" quietly costs 13x what "per Roundtable" costs, for the
# same information — and it defeats INCLUDE_SOURCE_LINK=0, since the model
# writes the domain into the tweet body where no link-append gate can see it.
_TLD = re.compile(r"\.(?:io|com|net|org|co|uk|tv|us|gg|app|news)$", re.I)


def _publisher_name(src: str) -> str:
    """A source name safe to drop into tweet text without X linkifying it."""
    name = (src or "").strip()
    if not name or " " in name:  # a real name already ("The Athletic")
        return name
    stripped = _TLD.sub("", name)
    if stripped == name:  # no tld — nothing to defuse
        return name
    stripped = stripped.replace("-", " ").strip()
    if not stripped:
        return name
    # Short all-lowercase remnants are acronyms ("nba" -> "NBA", not "Nba").
    return stripped.upper() if len(stripped) <= 4 else stripped[:1].upper() + stripped[1:]


# Stopwords + generic NBA/newswire tokens that carry no story-identity, dropped
# when building the dedup signature so two outlets' wording collapses to one key.
_STOP = {
    "the", "a", "an", "to", "of", "for", "and", "on", "in", "is", "are", "be",
    "with", "as", "at", "his", "her", "after", "from", "into", "over", "will",
    "nba", "report", "reports", "reportedly", "per", "via", "sources", "source",
    "news", "update", "says", "said",
}


def content_key(title: str) -> str:
    """An order- and attribution-independent signature of a headline's
    meaningful words. Two near-identical headlines ('LeBron James traded to
    Warriors' vs 'LeBron James traded to Warriors, per ESPN') collapse to the
    same key, so the same story reposted across outlets/feeds only tweets once.
    Full paraphrases with different verbs won't collapse — this catches the
    common aggregator repost, not every semantic duplicate."""
    toks = re.findall(r"[a-z0-9]+", (title or "").lower())
    toks = [w for w in toks if len(w) > 3 and w not in _STOP]
    return " ".join(sorted(set(toks))[:10])


# Per-feed health from the most recent fetch_all() call: a list of
# {source, url, ok, count, newest_ts, error}. The dashboard shows this as the
# "sources" list so you can see at a glance which feeds are live vs. quiet/down.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

LAST_HEALTH: list[dict] = []


def _fetch_one(source: str, url: str) -> tuple[list[NewsItem], dict]:
    """Fetch and normalize a single feed. Never raises: a dead feed returns no
    items and an error in its health row, so it can't take the cycle down."""
    items: list[NewsItem] = []
    ok, err, entries = True, "", []
    try:
        # Identify as a browser. feedparser's default agent announces itself as
        # a bot, which is a free reason for Google News to refuse a request it
        # was already inclined to rate-limit. This is not what caused the
        # 2026-07-21 blackout — the identical URLs worked from a laptop on the
        # default agent, so that was the runner's IP — but there is no upside
        # to volunteering "I am a script" to a host that is already throttling.
        feed = feedparser.parse(url, agent=_USER_AGENT)
        entries = feed.entries[:20]
        if getattr(feed, "bozo", 0) and not entries:
            ok, err = False, "parse error"
    except Exception as e:
        ok, err, entries = False, str(e)[:80], []
    count, newest = 0, 0.0
    is_gnews = "news.google.com" in url
    for entry in entries:
        eid = _entry_id(entry)
        if not eid:
            continue
        title = _clean(entry.get("title") or "")
        src = source
        if is_gnews:
            # Google News aggregates every outlet; the real publisher is in
            # the per-item <source> element (fallback: the ' - Outlet' title
            # suffix). Resolve it so attribution reads 'per ESPN', not
            # 'per Google News', and strip the suffix from the headline.
            esrc = entry.get("source")
            if isinstance(esrc, dict) and esrc.get("title"):
                src = esrc["title"].strip()
            elif " - " in title:
                src = title.rsplit(" - ", 1)[1].strip()
            src = _publisher_name(src)
            if " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()
        ts = _entry_ts(entry)
        count += 1
        newest = max(newest, ts)
        items.append(
            NewsItem(
                id=eid,
                source=src,
                title=title,
                summary=_clean(entry.get("summary") or "")[:1500],
                link=entry.get("link") or "",
                published_ts=ts,
            )
        )
    return items, {"source": source, "url": url, "ok": ok,
                   "count": count, "newest_ts": newest, "error": err}


def fetch_all() -> list[NewsItem]:
    """Fetch every configured feed CONCURRENTLY. Feeds that error are skipped
    (one dead feed shouldn't stall the loop). Records per-feed health in
    LAST_HEALTH as a side effect.

    Concurrency is the difference between reacting to a scoop and missing it:
    fetched one at a time the 14 feeds take ~7.4s of pure network wait per
    cycle, and a feed that is timing out spends that budget alone while every
    other feed sits behind it. In parallel the same sweep is ~1.0s, bounded by
    the slowest single feed rather than their sum. It is all network wait, so
    threads are the right tool despite the GIL. Results are reassembled in
    FEEDS order so the health view and item order stay deterministic."""
    results: list[tuple[list[NewsItem], dict] | None] = [None] * len(FEEDS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(FEEDS) or 1)) as ex:
        futures = {ex.submit(_fetch_one, s, u): i for i, (s, u) in enumerate(FEEDS)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # _fetch_one is already total; belt and braces
                src, url = FEEDS[i]
                results[i] = ([], {"source": src, "url": url, "ok": False,
                                   "count": 0, "newest_ts": 0.0, "error": str(e)[:80]})

    items: list[NewsItem] = []
    health: list[dict] = []
    for r in results:
        if r is None:
            continue
        items.extend(r[0])
        health.append(r[1])
    global LAST_HEALTH
    LAST_HEALTH = health
    # Newest first: on a breaking-news account the freshest item should go out
    # first, and if we hit the daily cap it's the stale tail that gets dropped,
    # never the latest story.
    items.sort(key=lambda i: i.published_ts, reverse=True)
    return items


def is_fresh(item: NewsItem, max_age_seconds: float | None = None) -> bool:
    """Only tweet items published recently — stale news gets no engagement.
    Defaults to config.FRESH_MAX_AGE_MIN. Items with no timestamp are treated
    as fresh (dedup still protects us)."""
    if max_age_seconds is None:
        max_age_seconds = FRESH_MAX_AGE_MIN * 60
    if item.published_ts == 0:
        return True
    return (time.time() - item.published_ts) <= max_age_seconds
