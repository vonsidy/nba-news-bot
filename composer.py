"""Turn a news item into a tweet using the Claude API."""

import json

import anthropic

from config import ANTHROPIC_MODEL
from sources import NewsItem

client = anthropic.Anthropic()

# Per-process token accounting so a run can report exactly what it spent on
# Claude (each `--once` cron invocation is a fresh process, so this is per-cycle).
USAGE = {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def _account(resp) -> None:
    u = getattr(resp, "usage", None)
    if not u:
        return
    USAGE["calls"] += 1
    USAGE["input"] += getattr(u, "input_tokens", 0) or 0
    USAGE["output"] += getattr(u, "output_tokens", 0) or 0
    USAGE["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    USAGE["cache_creation"] += getattr(u, "cache_creation_input_tokens", 0) or 0

SYSTEM_PROMPT = """You write tweets for an automated NBA breaking-news account.

Editorial rules (non-negotiable):
- Report only what the source item actually says. Never invent, exaggerate, or
  speculate beyond it. If the item is thin, that's fine — a short accurate tweet
  beats an embellished one.
- Classify every item honestly:
  * official  — confirmed/announced by a team, the league, or the player
  * report    — a reporter's sourced story ("per @ShamsCharania", "ESPN reports")
  * rumor     — unconfirmed chatter, trade speculation, "sources say" aggregation
  * highlight — a standout individual performance by a star/notable player (see below)
  * final     — a game that JUST ended, when the item states the final score
- Rumors and reports MUST name the source in the tweet ("per ESPN", "via HoopsHype").
- Free-agency and trade CHATTER about a notable player IS newsworthy — a star
  reportedly deciding today, weighing offers, drawing interest from teams, or
  requesting a trade. Post it as category "rumor" (or "report" if a named
  reporter has it), with the source named. This build-up drives real engagement
  — don't skip it as "thin." But only ever say what the source actually says:
  never invent a rumor, a destination, or a timeline that isn't in the item.
- Skip items that aren't real NBA news: betting-odds content, listicles,
  "where to watch" guides, fantasy advice, sponsored posts.
- Skip long-form game recaps and box-score breakdowns. BUT a just-finished
  game's final score IS newsworthy: when the headline/summary gives both teams
  and the final score (e.g. "Wizards beat Hawks 91-83"), use category "final",
  fill away_team/home_team/away_score/home_score (the WINNER's score goes with
  the winning team — copy the numbers exactly, never guess), and write a short
  punchy result tweet. If you can't tell both scores for certain, skip it.
  Also set star_player to the game's standout/leading player if the item names
  one (e.g. the player with the big stat line), else a well-known star on the
  winning team, else empty — it's used to pull a photo for the card backdrop.
- HIGHLIGHT posts are allowed ONLY for a standout individual performance by a
  genuine NBA STAR or a highly-touted prospect (e.g. a top summer-league rookie):
  a big scoring night, a triple-double, a game-winner, a breakout game. A role
  player's ordinary stat line is NOT newsworthy — skip it. Set is_star=true only
  when the player is a widely-known star or a high draft pick / top prospect;
  when in doubt whether they're a star, set is_star=false (the bot will skip it).

Style:
- Punchy and fast, like a breaking-news wire account with personality.
- Prefix by category: official -> "🚨 OFFICIAL:", report -> "📰 REPORT:", rumor -> "👀 RUMOR:", highlight -> "🔥"
- Max 250 characters (leaves room for a source link when INCLUDE_SOURCE_LINK is on).
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
        "category": {"type": "string", "enum": ["official", "report", "rumor", "highlight", "final", "skip"]},
        "tweet": {
            "type": "string",
            "description": "The tweet text, max 250 characters. Empty string if not newsworthy.",
        },
        "is_trade": {
            "type": "boolean",
            "description": "true only if this item is about a specific player being traded, signed, or moving to a new team",
        },
        "is_highlight": {
            "type": "boolean",
            "description": "true if this is a standout individual game performance (highlight), not a transaction or injury",
        },
        "is_star": {
            "type": "boolean",
            "description": "true only if the player is a widely-known NBA star or a top prospect/high draft pick; false for role players or unknown names",
        },
        "player": {
            "type": "string",
            "description": "The PRIMARY player this item is about (full name), for ANY category — trade, rumor, report, or highlight (e.g. the star reportedly deciding, the player being traded, the standout performer). Empty ONLY if the item isn't centered on one specific player.",
        },
        "from_team": {
            "type": "string",
            "description": "The team the player is leaving (name, nickname, or 3-letter abbreviation) if known, else empty string",
        },
        "to_team": {
            "type": "string",
            "description": "The team the player is going to (name, nickname, or 3-letter abbreviation) if is_trade, else empty string",
        },
        "away_team": {
            "type": "string",
            "description": "category=final only: the road team (name/nickname/abbreviation), else empty string",
        },
        "home_team": {
            "type": "string",
            "description": "category=final only: the home team (name/nickname/abbreviation), else empty string",
        },
        "away_score": {
            "type": "integer",
            "description": "category=final only: the road team's final points, copied exactly from the item; else 0",
        },
        "home_score": {
            "type": "integer",
            "description": "category=final only: the home team's final points, copied exactly from the item; else 0",
        },
        "star_player": {
            "type": "string",
            "description": "category=final only: the game's standout/leading player (full name) for the card backdrop photo, else empty string",
        },
    },
    "required": ["newsworthy", "category", "tweet", "is_trade", "is_highlight", "is_star", "player", "from_team", "to_team", "away_team", "home_team", "away_score", "home_score", "star_player"],
    "additionalProperties": False,
}


def compose(item: NewsItem) -> dict | None:
    """Returns {"newsworthy": bool, "category": str, "tweet": str} or None on failure."""
    user_msg = (
        f"Source: {item.source}\n"
        f"Headline: {item.title}\n"
        f"Summary: {item.summary}\n"
    )
    # "low" effort keeps latency/cost down and is plenty for classify + rewrite.
    # Not every model accepts the effort hint, so if the request is rejected for
    # it we retry once without it — otherwise a model swap could silently stop
    # the whole bot (every compose returning None = nothing ever posts).
    fmt = {"type": "json_schema", "schema": TWEET_SCHEMA}
    configs = [{"effort": "low", "format": fmt}, {"format": fmt}]
    # SYSTEM_PROMPT is byte-identical every call, so mark a cache breakpoint.
    # NOTE: Haiku 4.5's minimum cacheable prefix is 2048 tokens; this prompt is
    # ~700, so this is likely a no-op (cache_creation stays 0) — USAGE logging
    # tells us empirically. Harmless either way.
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    response = None
    for i, output_config in enumerate(configs):
        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1024,
                output_config=output_config,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            _account(response)
            break
        except anthropic.RateLimitError:
            print("  Claude API rate limited — skipping this cycle's item")
            return None
        except anthropic.APIStatusError as e:
            # On the first try, a 4xx may just be the effort hint — retry lean.
            if i == 0 and 400 <= e.status_code < 500:
                continue
            print(f"  Claude API error {e.status_code}: {e.message}")
            return None
        except anthropic.APIConnectionError:
            print("  Network error reaching the Claude API")
            return None
    if response is None:
        return None

    if response.stop_reason == "refusal":
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
