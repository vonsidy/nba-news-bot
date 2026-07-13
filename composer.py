"""Turn a news item into a tweet using the Claude API."""

import json

import anthropic

from config import ANTHROPIC_MODEL
from sources import NewsItem

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You write tweets for an automated NBA breaking-news account.

Editorial rules (non-negotiable):
- Report only what the source item actually says. Never invent, exaggerate, or
  speculate beyond it. If the item is thin, that's fine — a short accurate tweet
  beats an embellished one.
- Classify every item honestly:
  * official — confirmed/announced by a team, the league, or the player
  * report   — a reporter's sourced story ("per @ShamsCharania", "ESPN reports")
  * rumor    — unconfirmed chatter, trade speculation, "sources say" aggregation
- Rumors and reports MUST name the source in the tweet ("per ESPN", "via HoopsHype").
- Skip items that aren't real NBA news: betting-odds content, listicles,
  "where to watch" guides, fantasy advice, old recaps, sponsored posts.

Style:
- Punchy and fast, like a breaking-news wire account with personality.
- Prefix by category: official -> "🚨 OFFICIAL:", report -> "📰 REPORT:", rumor -> "👀 RUMOR:"
- Max 250 characters (a link gets appended after you, which costs 24).
- At most one hashtag, only if it's a big story (e.g. #NBATrade). No hashtag spam.
- No first person, no questions to the audience, no engagement-bait phrases.

Trade detection (for auto-generating a graphic):
- Set is_trade=true only when a SPECIFIC named player is confirmed/reported to be
  changing teams (traded, signed, waived-and-claimed). General trade rumors with no
  named destination, or non-movement news, are is_trade=false.
- When is_trade, fill player (full name), from_team, and to_team. Prefer the 3-letter
  NBA abbreviation (e.g. BOS, PHI, LAL) but a nickname or city is fine. Leave from_team
  empty if the origin team isn't stated."""

TWEET_SCHEMA = {
    "type": "object",
    "properties": {
        "newsworthy": {
            "type": "boolean",
            "description": "false if the item should be skipped per the editorial rules",
        },
        "category": {"type": "string", "enum": ["official", "report", "rumor", "skip"]},
        "tweet": {
            "type": "string",
            "description": "The tweet text, max 250 characters. Empty string if not newsworthy.",
        },
        "is_trade": {
            "type": "boolean",
            "description": "true only if this item is about a specific player being traded, signed, or moving to a new team",
        },
        "player": {
            "type": "string",
            "description": "The player's full name if is_trade, else empty string",
        },
        "from_team": {
            "type": "string",
            "description": "The team the player is leaving (name, nickname, or 3-letter abbreviation) if known, else empty string",
        },
        "to_team": {
            "type": "string",
            "description": "The team the player is going to (name, nickname, or 3-letter abbreviation) if is_trade, else empty string",
        },
    },
    "required": ["newsworthy", "category", "tweet", "is_trade", "player", "from_team", "to_team"],
    "additionalProperties": False,
}


def compose(item: NewsItem) -> dict | None:
    """Returns {"newsworthy": bool, "category": str, "tweet": str} or None on failure."""
    user_msg = (
        f"Source: {item.source}\n"
        f"Headline: {item.title}\n"
        f"Summary: {item.summary}\n"
    )
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            # Speed matters for breaking news; "low" keeps latency down and is
            # plenty for a classification + 250-char rewrite task.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": TWEET_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.RateLimitError:
        print("  Claude API rate limited — skipping this cycle's item")
        return None
    except anthropic.APIStatusError as e:
        print(f"  Claude API error {e.status_code}: {e.message}")
        return None
    except anthropic.APIConnectionError:
        print("  Network error reaching the Claude API")
        return None

    if response.stop_reason == "refusal":
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
