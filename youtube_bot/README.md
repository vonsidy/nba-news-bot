# YouTube Shorts Story Bot

Fully-automated pipeline that writes a short story, narrates it, lays it over
gameplay footage with animated captions, and uploads it to YouTube as a Short.

```
story.py    -> Claude writes story + title + description + tags
voice.py    -> TTS voiceover + per-word timing (edge-tts free, or ElevenLabs)
assemble.py -> FFmpeg: gameplay bg + burned captions + audio -> 1080x1920 mp4
youtube_upload.py -> YouTube Data API v3 upload
run.py      -> ties it all together
```

## One-time setup

1. **Install FFmpeg** and put it on PATH. On Windows:
   `winget install Gyan.FFmpeg`  (then restart the terminal)

2. **Install Python deps:**
   `pip install -r youtube_bot/requirements.txt`

3. **Add background gameplay.** Drop one or more vertical (or any) gameplay
   `.mp4` files into `backgrounds/`. Record your own Subway Surfers / Minecraft
   parkour / Roblox obby run (safest for copyright), or use clips marked
   reusable. One 10-20 min clip is enough — the bot slices a random segment
   per video.

4. **Set env vars** (in the repo `.env`, shared with the NBA bot):
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DRY_RUN=true                 # flip to false to actually upload
   STORY_THEME=short suspenseful first-person Reddit-style stories with a twist
   # optional paid voice:
   # TTS_PROVIDER=elevenlabs
   # ELEVEN_API_KEY=...
   # ELEVEN_VOICE_ID=...
   ```

5. **Authorize YouTube (one time):** create an OAuth *Desktop* client in Google
   Cloud Console (enable "YouTube Data API v3"), download it to
   `youtube_bot/client_secret.json`, then run:
   `python -m youtube_bot.youtube_upload --auth`

## Run it

```bash
# Build a video but don't upload (safe first test):
python -m youtube_bot.run --once --no-upload

# Full run incl. upload:
DRY_RUN=false python -m youtube_bot.run --once
```

Finished videos are written to `output/`.

## Notes / caveats
- Start at 3-5 uploads/day. Fully-automated mass uploading risks YouTube's
  spam / reused-content policies flagging monetization.
- The background footage must be something you have the right to use.
- Schedule it with GitHub Actions the same way the NBA bot runs (see
  `.github/workflows/`), running `python -m youtube_bot.run --once`.
