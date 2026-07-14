"""Text-to-speech + word-level caption timing.

Default provider is edge-tts (free, no API key). It conveniently emits
WordBoundary events, so we get accurate per-word timestamps for animated
captions without a separate speech-recognition step.
"""

import asyncio

import youtube_bot.config as config


def synthesize(script: str, out_mp3: str) -> list[dict]:
    """Generate voiceover audio at out_mp3.

    Returns a list of word timing dicts: {"word", "start", "end"} in seconds.
    """
    if config.TTS_PROVIDER == "elevenlabs":
        return _elevenlabs(script, out_mp3)
    return asyncio.run(_edge(script, out_mp3))


async def _edge(script: str, out_mp3: str) -> list[dict]:
    import edge_tts

    communicate = edge_tts.Communicate(
        script, config.EDGE_VOICE, boundary="WordBoundary"
    )
    words: list[dict] = []
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7          # 100ns ticks -> seconds
                dur = chunk["duration"] / 1e7
                words.append(
                    {
                        "word": chunk["text"],
                        "start": round(start, 3),
                        "end": round(start + dur, 3),
                    }
                )
    return words


def _elevenlabs(script: str, out_mp3: str) -> list[dict]:
    """Paid path. Uses the with-timestamps endpoint for word timing."""
    import base64

    import requests

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/"
        f"{config.ELEVEN_VOICE_ID}/with-timestamps"
    )
    r = requests.post(
        url,
        headers={"xi-api-key": config.ELEVEN_API_KEY},
        json={"text": script, "model_id": "eleven_turbo_v2_5"},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    with open(out_mp3, "wb") as f:
        f.write(base64.b64decode(data["audio_base64"]))

    # Character-level timings -> group into words on whitespace.
    chars = data["alignment"]["characters"]
    starts = data["alignment"]["character_start_times_seconds"]
    ends = data["alignment"]["character_end_times_seconds"]
    words: list[dict] = []
    cur, cur_start = "", None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": cur_start, "end": prev_e})
                cur, cur_start = "", None
        else:
            if cur_start is None:
                cur_start = s
            cur += ch
            prev_e = e
    if cur:
        words.append({"word": cur, "start": cur_start, "end": prev_e})
    return words


if __name__ == "__main__":
    import os

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    out = os.path.join(config.OUTPUT_DIR, "_voice_test.mp3")
    w = synthesize("This is a test. The twist is that it worked.", out)
    print(f"Wrote {out} with {len(w)} word timings")
    print(w[:5])
