"""Fetch and normalize NBA news items from RSS feeds."""

import calendar
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
LAST_HEALTH: list[dict] = []


def fetch_all() -> list[NewsItem]:
    """Fetch every configured feed. Feeds that error are skipped silently
    (one dead feed shouldn't stall the loop). Records per-feed health in
    LAST_HEALTH as a side effect."""
    items: list[NewsItem] = []
    health: list[dict] = []
    for source, url in FEEDS:
        ok, err, entries = True, "", []
        try:
            feed = feedparser.parse(url)
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
        health.append({"source": source, "url": url, "ok": ok,
                       "count": count, "newest_ts": newest, "error": err})
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
