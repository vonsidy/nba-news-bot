"""Generate a short story + YouTube metadata with Claude."""

import json
import random

from anthropic import Anthropic

import youtube_bot.config as config

_client = None


def _client_lazy():
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


SYSTEM = """You are a viral YouTube Shorts scriptwriter. You write ultra-engaging
first-person short stories designed to stop the scroll. Rules:
- The FIRST sentence must be a shocking hook that creates a question in the viewer's mind.
- One single clean twist or payoff near the end.
- Conversational, spoken English. Short sentences. No stage directions, no emojis in the script.
- End with a one-line call to action that invites a comment.
Return ONLY valid JSON, no markdown fences."""


def generate(theme: str | None = None, target_seconds: int | None = None) -> dict:
    """Return dict: {title, script, description, tags, hook}.

    If no theme is passed, uses the STORY_THEME env override when set, else
    picks a random style from config.STORY_THEMES so videos stay varied.
    """
    theme = theme or config.STORY_THEME or random.choice(config.STORY_THEMES)
    target_seconds = target_seconds or config.TARGET_SECONDS
    # ~2.6 spoken words/sec is a natural narration pace.
    word_budget = int(target_seconds * 2.6)

    prompt = f"""Write a {theme}.

Constraints:
- The spoken script must be about {word_budget} words (target {target_seconds}s of narration).
- Make it feel real and specific, not generic.

Return JSON with these exact keys:
{{
  "title": "clickable YouTube title under 90 chars, no clickbait lies",
  "hook": "the first line of the script, repeated here",
  "script": "the full narration text to be read aloud",
  "description": "2-3 sentence YouTube description",
  "tags": ["8-12", "lowercase", "search", "keywords"]
}}"""

    resp = _client_lazy().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    data = json.loads(text)
    # Basic validation.
    for key in ("title", "script", "description", "tags"):
        if key not in data:
            raise ValueError(f"Claude response missing key: {key}")
    return data


if __name__ == "__main__":
    import pprint

    pprint.pprint(generate())
