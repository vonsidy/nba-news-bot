"""Assemble the final 9:16 Short with FFmpeg.

Takes: voiceover mp3 + word timings + a random background gameplay clip.
Produces: a 1080x1920 mp4 with the gameplay behind, audio on top, and
animated word-group captions burned in (TikTok/Shorts style).
"""

import os
import random
import subprocess

import youtube_bot.config as config


def _ffprobe_duration(path: str) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        text=True,
    )
    return float(out.strip())


def _pick_background() -> str:
    clips = [
        os.path.join(config.BACKGROUNDS_DIR, f)
        for f in os.listdir(config.BACKGROUNDS_DIR)
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
    ]
    if not clips:
        raise FileNotFoundError(
            f"No background clips in {config.BACKGROUNDS_DIR}. "
            "Add at least one gameplay .mp4 (e.g. Subway Surfers / Minecraft parkour)."
        )
    return random.choice(clips)


def _fmt_time(t: float) -> str:
    """seconds -> ASS h:mm:ss.cs"""
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass(words: list[dict], ass_path: str, group_size: int = 3):
    """Write an ASS subtitle file that shows groups of words, synced to speech."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,Anton,130,&H00FFFFFF,&H00000000,&H00000000,-1,0,1,7,3,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for i in range(0, len(words), group_size):
        group = words[i : i + group_size]
        start = group[0]["start"]
        end = group[-1]["end"]
        text = " ".join(w["word"] for w in group).upper().replace("\n", " ")
        # pop-in scale animation
        effect = "{\\fad(60,60)\\t(0,120,\\fscx110\\fscy110)\\t(120,220,\\fscx100\\fscy100)}"
        lines.append(
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Pop,,0,0,0,,{effect}{text}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build(voice_mp3: str, words: list[dict], out_mp4: str) -> str:
    audio_dur = _ffprobe_duration(voice_mp3)
    total = audio_dur + 0.6  # small tail

    bg = _pick_background()
    bg_dur = _ffprobe_duration(bg)
    start = round(random.uniform(0, max(0, bg_dur - total)), 2) if bg_dur > total else 0

    ass_path = out_mp4.replace(".mp4", ".ass")
    _build_ass(words, ass_path)
    # FFmpeg needs an escaped path for the subtitles filter on Windows.
    ass_filter = ass_path.replace("\\", "/").replace(":", "\\:")
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    fonts_arg = fonts_dir.replace("\\", "/").replace(":", "\\:")

    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,"
        f"subtitles='{ass_filter}':fontsdir='{fonts_arg}'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-t", f"{total:.2f}", "-i", bg,
        "-i", voice_mp3,
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-shortest", out_mp4,
    ]
    subprocess.run(cmd, check=True)
    return out_mp4
