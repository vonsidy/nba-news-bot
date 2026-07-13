"""Fetch and normalize NBA news items from RSS feeds."""

import calendar
import time
from dataclasses import dataclass

import feedparser

from config import FEEDS


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


def fetch_all() -> list[NewsItem]:
    """Fetch every configured feed. Feeds that error are skipped silently
    (one dead feed shouldn't stall the loop)."""
    items: list[NewsItem] = []
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries[:20]:
            eid = _entry_id(entry)
            if not eid:
                continue
            items.append(
                NewsItem(
                    id=eid,
                    source=source,
                    title=(entry.get("title") or "").strip(),
                    summary=(entry.get("summary") or "").strip()[:1500],
                    link=entry.get("link") or "",
                    published_ts=_entry_ts(entry),
                )
            )
    # Oldest first so tweets go out in chronological order
    items.sort(key=lambda i: i.published_ts)
    return items


def is_fresh(item: NewsItem, max_age_seconds: float = 6 * 3600) -> bool:
    """Only tweet items published recently — stale news gets no engagement.
    Items with no timestamp are treated as fresh (dedup still protects us)."""
    if item.published_ts == 0:
        return True
    return (time.time() - item.published_ts) <= max_age_seconds
