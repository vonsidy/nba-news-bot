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
- The ARTICLE being new does not make the NEWS new. Set newsworthy=false when
  the item is a later write-up of something that already happened — pick
  details or financial terms revealed after the fact, grades and reaction,
  "what it means for", "revisiting", anniversary and look-back pieces. A
  breaking-news account posts the trade when it breaks, not the analysis a week
  later. If the item does not assert that something happened recently, skip it.
- When the source states CONTRACT TERMS — years, total value, guarantees, player
  or team option — put them in the tweet. "two years, $8.4M" is the detail
  followers actually want, and there is room for it. Copy the figure exactly as
  the source gives it; never estimate, round, convert, or infer a number the
  source did not state. Most two-way and 10-day deals carry no figure at all
  because the value is set by the league — say nothing about money on those
  rather than reaching for one.
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
        # Descriptions are deliberately terse. Every rule these used to restate
        # — what counts as a trade, who counts as a star, when to skip, how to
        # pick a category — is stated in full in SYSTEM_PROMPT above, and the
        # schema is re-sent on EVERY call. Saying it twice cost ~370 tokens a
        # call for nothing: the fixed prompt+schema is ~95% of each request and
        # the actual news is ~5%. Field names plus the prompt carry the
        # meaning; these lines only disambiguate shape and units.
        "newsworthy": {"type": "boolean", "description": "false = skip, per the editorial rules"},
        "category": {"type": "string", "enum": ["official", "report", "rumor", "highlight", "final", "skip"]},
        "tweet": {"type": "string", "description": "Max 250 chars. Empty if not newsworthy."},
        "is_trade": {"type": "boolean", "description": "player traded/signed/moving teams"},
        "is_highlight": {"type": "boolean", "description": "standout game performance, not a transaction"},
        "is_star": {"type": "boolean", "description": "widely-known star or top prospect"},
        "player": {
            "type": "string",
            "description": "PRIMARY player, full name, any category. Empty only if not about one player.",
        },
        "from_team": {"type": "string", "description": "team being left; name/nickname/abbrev, else ''"},
        "to_team": {"type": "string", "description": "team being joined; name/nickname/abbrev, else ''"},
        "away_team": {"type": "string", "description": "final only: road team, else ''"},
        "home_team": {"type": "string", "description": "final only: home team, else ''"},
        "away_score": {"type": "integer", "description": "final only: road points as printed, else 0"},
        "home_score": {"type": "integer", "description": "final only: home points as printed, else 0"},
        "star_player": {"type": "string", "description": "final only: standout player for the card photo, else ''"},
    },
    "required": ["newsworthy", "category", "tweet", "is_trade", "is_highlight", "is_star", "player", "from_team", "to_team", "away_team", "home_team", "away_score", "home_score", "star_player"],
    "additionalProperties": False,
}


# ---- Batching ---------------------------------------------------------------
# The schema note above got the diagnosis right and the fix half-right: trimming
# the descriptions removed ~370 tokens, but SYSTEM_PROMPT + TWEET_SCHEMA are
# still 1,637 tokens against a ~64-token headline, and they were re-sent on
# every single call.
#
# Prompt caching cannot recover that: Haiku 4.5 will not cache a prefix shorter
# than 4096 tokens, and this one is 1,637. The cache_control breakpoint that
# used to sit on the system block was a no-op from the day it landed — its own
# comment quoted a 2048 minimum, which is also wrong. Removed rather than left
# in looking load-bearing; cache_creation had logged 0 the whole time.
#
# So amortise it instead. One call carries many items, and the fixed 1,637
# tokens is paid once for the batch rather than once per headline. Measured on
# the live feeds at 25 items/call: 1,701 -> 133 input tokens per item, 4.2x
# cheaper overall. It is 4.2x and not 12x because OUTPUT tokens (~70/item, at
# 5x the input rate) do not amortise — every item still gets its own answer.
#
# What that buys is coverage, not a smaller bill: the same $0.166/day ceiling
# goes from ~81 headlines to ~344, which is more than the free prefilters let
# through. The bot stops rationing.

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
            "description": "One object per input item.",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "the item number this answers"},
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
    # Not every model accepts the effort hint, so if the request is rejected for
    # it we retry once without it — otherwise a model swap could silently stop
    # the whole bot (every compose returning None = nothing ever posts). That
    # wasted round-trip is now once per BATCH rather than once per headline.
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
    # A truncated reply is unparseable JSON anyway, but name it: a silent None
    # here is indistinguishable from "nothing was newsworthy".
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

    Still the per-item path, and the fallback for anything compose_batch()
    could not answer for.
    """
    return _call(SYSTEM_PROMPT, _render(item), TWEET_SCHEMA, 1024)


def compose_batch(items: list[NewsItem]) -> list[dict | None]:
    """Classify + rewrite several items in a single call.

    Returns a list positionally aligned with `items` (None where no verdict came
    back). Anything the batch did not answer for is retried individually, so a
    malformed or partial reply costs money, never coverage.
    """
    if not items:
        return []
    if len(items) == 1:
        return [compose(items[0])]

    user_msg = "\n".join(f"Item {n}:\n{_render(it)}" for n, it in enumerate(items))
    # ~200 output tokens per item in the worst case (14 fields + a 250-char
    # tweet); 400 leaves room, and 8192 caps a runaway.
    data = _call(SYSTEM_PROMPT + _BATCH_ADDENDUM, user_msg, BATCH_SCHEMA,
                 min(8192, 400 * len(items) + 512))

    out: list[dict | None] = [None] * len(items)
    for r in (data or {}).get("results") or []:
        # Trust the echoed index, never the position. A dropped or reordered
        # result would otherwise shift every later verdict onto the wrong
        # headline — worse than losing one, because it publishes the wrong
        # tweet for a real story.
        n = r.get("index")
        if isinstance(n, int) and 0 <= n < len(items) and out[n] is None:
            out[n] = r

    missing = [n for n, r in enumerate(out) if r is None]
    if missing:
        print(f"  batch answered {len(items) - len(missing)}/{len(items)}; "
              f"retrying {len(missing)} individually")
        for n in missing:
            out[n] = compose(items[n])
    return out
