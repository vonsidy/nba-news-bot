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


# ---- Batching ---------------------------------------------------------------
# SYSTEM_PROMPT + TWEET_SCHEMA are ~1,600 tokens and identical on every request;
# a headline+summary is ~64. One call per item therefore spent ~96% of its input
# re-sending the same instructions, and prompt caching can't rescue that (Haiku
# 4.5 won't cache a prefix under 4096 tokens — this one is well under).
#
# So we send many items per call instead. The overhead is paid once per batch,
# which drops per-item input from ~1,650 tokens to ~150, and collapses a cycle's
# N sequential round-trips into one.

_BATCH_ADDENDUM = """

You will be given SEVERAL numbered news items in one message. Apply the rules
above to each item INDEPENDENTLY — judge every item on its own content only, and
never let one item influence the verdict on another.

Return one result object per item in "results", each carrying the "index" of the
item it describes. Include EVERY item you were given, even the ones that aren't
newsworthy (return those with newsworthy=false and an empty tweet)."""

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "description": "One object per input item, in the order given.",
            "items": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "The number of the item this result is for, as labelled in the message.",
                    },
                    **TWEET_SCHEMA["properties"],
                },
                "required": ["index", *TWEET_SCHEMA["required"]],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _render(item: NewsItem) -> str:
    return (
        f"Source: {item.source}\n"
        f"Headline: {item.title}\n"
        f"Summary: {item.summary}\n"
    )


def _call(system: str, user_msg: str, schema: dict, max_tokens: int) -> dict | None:
    """One Claude request returning parsed structured output, or None."""
    # "low" effort keeps latency/cost down and is plenty for classify + rewrite.
    # Not every model accepts the effort hint (Haiku 4.5 rejects it), so if the
    # request is refused for it we retry once without — otherwise a model swap
    # could silently stop the whole bot (every compose returning None = nothing
    # ever posts). One wasted round-trip per BATCH now, not per item.
    fmt = {"type": "json_schema", "schema": schema}
    configs = [{"effort": "low", "format": fmt}, {"format": fmt}]
    response = None
    for i, output_config in enumerate(configs):
        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                output_config=output_config,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            _account(response)
            break
        except anthropic.RateLimitError:
            print("  Claude API rate limited — skipping")
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
    if response is None or response.stop_reason == "refusal":
        return None
    # A truncated reply is unparseable JSON anyway, but say so plainly — a silent
    # None here would look identical to "nothing was newsworthy".
    if response.stop_reason == "max_tokens":
        print("  Claude reply hit max_tokens (batch too large?) — falling back")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def compose(item: NewsItem) -> dict | None:
    """Classify + rewrite ONE item. Returns the result dict, or None on failure.

    Kept as the per-item fallback for anything compose_batch() couldn't answer.
    """
    return _call(SYSTEM_PROMPT, _render(item), TWEET_SCHEMA, 1024)


def compose_batch(items: list[NewsItem]) -> list[dict | None]:
    """Classify + rewrite up to CLAUDE_BATCH_SIZE items in a single call.

    Returns a list positionally aligned with `items` (None where no verdict came
    back). Any item the batch didn't answer for is retried individually, so a
    malformed or partial reply costs money, never coverage.
    """
    if not items:
        return []
    if len(items) == 1:
        return [compose(items[0])]

    user_msg = "\n".join(f"Item {n}:\n{_render(it)}" for n, it in enumerate(items))
    # ~200 output tokens per item (14 fields + a 250-char tweet), plus headroom.
    data = _call(SYSTEM_PROMPT + _BATCH_ADDENDUM, user_msg, BATCH_SCHEMA,
                 min(8192, 400 * len(items) + 512))

    out: list[dict | None] = [None] * len(items)
    for r in (data or {}).get("results") or []:
        # Trust the index, not the position: a dropped or reordered result would
        # otherwise shift every verdict after it onto the wrong headline, which
        # is far worse than losing one — it posts the wrong tweet for a story.
        n = r.get("index")
        if isinstance(n, int) and 0 <= n < len(items) and out[n] is None:
            out[n] = r

    missing = [n for n, r in enumerate(out) if r is None]
    if missing:
        print(f"  batch returned {len(items) - len(missing)}/{len(items)}; "
              f"retrying {len(missing)} individually")
        for n in missing:
            out[n] = compose(items[n])
    return out
