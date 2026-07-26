"""One opinion post a day, grounded in the day's real NBA news.

This replaces the evergreen debate cards in engage.py, which the owner turned
off because they read as filler. They were filler for two structural reasons,
and both are fixed here:

  1. They were CANNED. Six fixed templates over a hand-typed star pool, chosen
     by a seeded RNG. Nothing about the post knew what day it was, so it could
     not be about anything. "Rank these 6" on the day Giannis got traded is
     visibly a bot filling space.
  2. The pool went STALE and nobody noticed, because no code path ever compared
     it to reality. It still lists LeBron on the Lakers, Giannis on the Bucks,
     Kawhi on the Clippers and LaMelo on the Hornets — every one of those wrong
     as of this offseason. A card confidently showing a player on a team he
     left is the same credibility failure as a wrong-person photo.

So a take here is built from the headlines the bot ALREADY fetched this cycle.
That makes it topical by construction, keeps the teams current because they come
from today's news rather than a literal, and costs one Claude call a day.

Why opinion at all: the account reprints news that Shams broke minutes earlier,
which gives nobody a reason to follow — the same fact is free, faster, upstream.
An argument is the one thing an aggregator can offer that the wire does not.

The hard constraint is that this account's whole value is being true. A take is
allowed to be WRONG in the sense that a reader disagrees; it is never allowed to
be wrong about a fact. So the model is given real headlines and told to reason
only from them, the output is checked against those headlines before it posts,
and the card is stamped HOT TAKE rather than BREAKING NEWS so no reader can
mistake an argument for a report.
"""

import re

import composer

# Kept deliberately small. This is one call a day and the batch machinery in
# composer is built for volume, so it is not reused — but the client, retry
# handling and error paths are, via composer._call.
MAX_HEADLINES = 30

SYSTEM_PROMPT = """You write ONE opinion post a day for @TheNBASignal, an NBA
news account. You will be given today's real NBA headlines.

Your job: pick the single most argued-about story in those headlines and state a
clear, confident opinion about it.

WHAT MAKES THIS GOOD:
- ARGUABLE. A well-informed fan should be able to disagree with you. "LeBron is
  a great player" is not a take. "LeBron to Philly makes them worse in the
  playoffs" is a take.
- SPECIFIC. Name the player, the team, the number. Vague takes get ignored.
- CONFIDENT. State it flat. No "maybe", no "it could be argued", no hedging.
- SHORT. 2-4 lines. The take should be readable in one glance.
- Ends with a line that invites disagreement ("Tell me I'm wrong.", "Who says
  no?"). Do NOT end with a generic question like "What do you think?".

ABSOLUTE RULES ON FACTS — this is a news account and its only asset is being
trusted:
- Every FACT you state must come from the headlines you were given. Names,
  teams, contract figures, trades, records.
- Invent NOTHING. No stats, no quotes, no injury news, no "sources say", no
  standings, no career averages, no past-season results that are not in the
  headlines.
- If you are not sure a player is currently on a team, do not say he is. The
  headlines are your only source of who plays where.
- The OPINION is yours. The FACTS are theirs. Never blur those.

Set worth_posting=false if the headlines are all minor transactions with nothing
genuinely arguable in them. A skipped day is much better than a weak take.

Write in plain text. No hashtags. No emoji. No "BREAKING". This is commentary
and must never look like a report."""

TAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "worth_posting": {
            "type": "boolean",
            "description": "false if nothing in today's headlines is worth arguing about",
        },
        "take": {
            "type": "string",
            "description": "The post itself, 2-4 short lines, ending on a line that invites disagreement",
        },
        "card_line": {
            "type": "string",
            "description": "The take compressed to under 60 characters for the card graphic, e.g. 'PHILLY STILL ISN'T WINNING THE EAST'",
        },
        "player": {
            "type": "string",
            "description": "The main player the take is about, for the photo. Empty if it is about a team.",
        },
        "teams": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Team names involved, for the card colours. From the headlines only.",
        },
    },
    "required": ["worth_posting", "take", "card_line", "player", "teams"],
    "additionalProperties": False,
}


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9$]+", (text or "").lower()))


def _invented_numbers(take: str, corpus: str) -> list:
    """Figures in the take that appear nowhere in the source headlines.

    A fabricated contract figure or stat line is the single most damaging thing
    this can emit — it is the kind of wrong that gets screenshotted. Numbers are
    also the easiest fabrication to detect mechanically, so they are checked
    directly rather than trusted to the prompt. Years (1900-2099) are exempt:
    "the 2026 offseason" is framing, not a claim about a fact.
    """
    hay = _tokens(corpus)
    bad = []
    for raw in re.findall(r"\$?\d[\d,.]*[mMkKbB%]?", take or ""):
        # Trailing sentence punctuation is not part of the figure. Without this
        # a take ending "...since 2019." yielded the token "2019." which then
        # failed the year test and was reported as a fabricated number.
        cleaned = raw.rstrip(".,")
        norm = cleaned.lower().lstrip("$").rstrip("%")
        if not norm:
            continue
        if re.fullmatch(r"(19|20)\d\d", norm):
            continue          # a year is framing, not a factual claim
        # Compare with AND without the currency mark: _tokens keeps "$" inside a
        # token, so the corpus holds "$8m" while the take yields "8m". Checking
        # only the bare form flagged every correctly-quoted contract figure.
        if norm in hay or f"${norm}" in hay:
            continue
        bad.append(cleaned)
    return bad


def pick_take(items) -> dict | None:
    """One take built from today's headlines, or None if there isn't one.

    `items` are this cycle's NewsItems. Returns the parsed take dict, already
    checked for invented figures.
    """
    seen, headlines = set(), []
    for i in items:
        t = (i.title or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        headlines.append(t)
        if len(headlines) >= MAX_HEADLINES:
            break
    if len(headlines) < 3:
        print("  opinion: too few headlines to build a take from")
        return None

    corpus = "\n".join(headlines)
    result = composer._call(
        SYSTEM_PROMPT,
        "Today's NBA headlines:\n\n"
        + "\n".join(f"- {h}" for h in headlines)
        + "\n\nWrite today's take.",
        TAKE_SCHEMA,
        max_tokens=700,
    )
    if not result:
        print("  opinion: no verdict from Claude")
        return None
    if not result.get("worth_posting"):
        print("  opinion: nothing worth arguing about in today's headlines")
        return None
    take = (result.get("take") or "").strip()
    if not take:
        return None

    invented = _invented_numbers(take, corpus)
    if invented:
        # Refuse rather than repair. A number the headlines do not contain is a
        # fabricated fact, and there is no safe way to guess what it should have
        # been — dropping the post costs one quiet day, publishing it costs the
        # account's credibility.
        print(f"  opinion: take cites figures absent from the headlines {invented}, skipping")
        return None
    result["take"] = take
    return result
