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
# The TLD list both this module and bot._delink match against. ONE list, because
# they are two halves of the same defence and a tld missing from either is a
# post billed at $0.200 instead of $0.015.
#
# It was 11 entries and it cost real money on 2026-07-21: "Sportsnet.ca" is a
# Canadian outlet, `ca` was not in the list, so the domain survived
# _publisher_name AND _delink, went out in the tweet body, and X linkified it.
# Enumerating "the tlds we happen to have seen" is the wrong shape — X linkifies
# every real tld, so anything not listed is a live 13x charge waiting for the
# first outlet that uses it. Country codes are the obvious gap in a league with
# Canadian teams and a global press.
_TLDS = (
    "io|com|net|org|co|uk|tv|us|gg|app|news|ca|de|fr|au|jp|in|it|es|nl|br|mx"
    "|ru|ch|se|no|fi|dk|pl|at|be|nz|za|ie|sg|hk|kr|cn|info|biz|me|cc|ly|to|sh"
    "|am|fm|gl|xyz|online|site|press|media|sport|sports|club|team|live|blog"
)
_TLD = re.compile(rf"(?:\.(?:{_TLDS}))+$", re.I)


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

# Sent with every feed request alongside _USER_AGENT. A real browser asking for
# a feed sends all of these; feedparser on its own sends none of them, which is
# a free tell for an edge deciding whether to serve a datacenter IP.
_REQUEST_HEADERS = {
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

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
        # A browser agent alone was not enough for ESPN, which served the
        # Actions runner a body that parsed to ZERO entries while the identical
        # URL returned 16 items from a laptop — the datacenter IP again, the
        # same shape as the Google News block. A request that carries no Accept
        # at all is the cheapest thing for an edge to refuse, so send the
        # headers a browser would.
        feed = feedparser.parse(url, agent=_USER_AGENT, request_headers=_REQUEST_HEADERS)
        entries = feed.entries[:20]
        status = getattr(feed, "status", 0)
        if getattr(feed, "bozo", 0) and not entries:
            ok, err = False, "parse error"
        elif not entries:
            # Zero entries used to leave ok=True, which the dashboard renders as
            # "stale" — indistinguishable from a genuinely quiet feed. ESPN sat
            # like that, count 0, for days. A news feed with nothing in it is a
            # failure; say so, and carry the HTTP status that explains it.
            ok, err = False, f"no entries (HTTP {status or '?'})"
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
    Defaults to config.FRESH_MAX_AGE_MIN.

    An item with NO timestamp is NOT fresh. It used to auto-pass on the theory
    that dedup would protect us, but dedup only stops the same story twice — it
    has nothing to say about age, and process_item's per-type age check is also
    skipped when published_ts is 0. A dateless item therefore bypassed BOTH
    freshness gates and could post news of any age. Rare (0 of 84 items on
    2026-07-22) but unbounded when it happens, and silent."""
    if max_age_seconds is None:
        max_age_seconds = FRESH_MAX_AGE_MIN * 60
    if item.published_ts == 0:
        return False
    return (time.time() - item.published_ts) <= max_age_seconds
