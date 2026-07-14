"""Orchestrator: generate story -> voiceover -> assemble -> upload.

Usage:
    python -m youtube_bot.run --once          # make + (maybe) upload one Short
    DRY_RUN=false python -m youtube_bot.run --once   # actually upload
    python -m youtube_bot.run --once --no-upload     # build only, keep the mp4
"""

import argparse
import datetime as dt
import os

import youtube_bot.config as config
from youtube_bot import assemble, story, voice


def _slug(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -" else "" for c in text)
    return "-".join(keep.lower().split())[:50] or "short"


def make_one(upload: bool = True) -> dict:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    print("1/4  Generating story with Claude...")
    data = story.generate()
    base = os.path.join(config.OUTPUT_DIR, f"{stamp}-{_slug(data['title'])}")
    print("     title:", data["title"])

    print("2/4  Synthesizing voiceover...")
    mp3 = base + ".mp3"
    words = voice.synthesize(data["script"], mp3)

    print("3/4  Assembling video with FFmpeg...")
    mp4 = base + ".mp4"
    assemble.build(mp3, words, mp4)
    print("     wrote", mp4)

    result = {"video": mp4, **data}

    if upload and not config.DRY_RUN:
        print("4/4  Uploading to YouTube...")
        from youtube_bot import youtube_upload

        vid = youtube_upload.upload(
            mp4, data["title"], data["description"], data["tags"]
        )
        result["youtube_id"] = vid
        print("     https://youtube.com/shorts/" + vid)
    else:
        print("4/4  Skipped upload (DRY_RUN or --no-upload). Video saved locally.")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Make a single Short")
    parser.add_argument("--no-upload", action="store_true", help="Build but never upload")
    args = parser.parse_args()
    make_one(upload=not args.no_upload)
