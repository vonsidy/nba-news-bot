# TheNBASignal — NBA Breaking News Bot

> 📊 **Engagement dashboard:** this repo also contains a Vercel web dashboard
> (`dashboard/`) where you connect your X account with one click and get
> stats + "when to post" recommendations. See [dashboard/README.md](dashboard/README.md).

Fully automated X (Twitter) account that watches NBA news feeds, writes tweets
with Claude, and posts them within minutes of a story breaking.

Every tweet is honestly labeled:

- 🚨 **OFFICIAL** — confirmed by a team, the league, or the player
- 📰 **REPORT** — a reporter's sourced story, with attribution ("per ESPN")
- 👀 **RUMOR** — unconfirmed chatter, always attributed to where it came from

The bot never invents news. Rumor-labeled posts are the engagement driver —
they're real rumors from real sources, credited. That's what keeps the account
monetizable instead of banned.

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

2. **Get API keys**
   - Claude API key: https://platform.claude.com → API Keys
   - X API: https://developer.x.com → create a Project + App (free tier works).
     In the app's *User authentication settings*, enable **Read and write**.
     Then generate: API Key + Secret, and Access Token + Secret.

3. **Configure**
   ```
   copy .env.example .env
   ```
   Fill in the keys in `.env`.

4. **Test in dry-run mode** (default — prints tweets instead of posting)
   ```
   python bot.py
   ```

5. **Go live**: set `DRY_RUN=false` in `.env` and run again. Keep it running
   with Task Scheduler, or just leave the terminal open.

## Staying compliant (this is what protects the revenue)

- **Label the account as automated.** X Settings → Your account →
  Account information → Automation. Required by X's automation rules;
  unlabeled bots get suspended.
- **Respect the caps.** `MAX_POSTS_PER_DAY=15` keeps you inside the free API
  tier (~500 posts/month) and below spam-detection thresholds.
- **Monetization requirements** (X Creator Revenue Sharing): Premium
  subscription, 500+ followers, and 5M organic impressions in the last
  3 months. Ad revenue sharing pays on verified users' engagement with your
  posts — so growth comes first, revenue later.

## Tuning

- `POLL_SECONDS` — lower = faster to break news, 60 is a reasonable floor.
- `config.py` → `FEEDS` — add/remove sources. Team-specific feeds work great
  for a niche account (often better engagement than league-wide).
- `composer.py` → `SYSTEM_PROMPT` — adjust the voice. Adding personality/humor
  is fine; the accuracy and attribution rules should stay.

## Trade alert graphics

When the bot detects a player trade, it auto-generates a **BREAKING NEWS** card
and attaches it to the tweet (`card.py`).

- It first tries to pull a **reuse-licensed player photo** (public domain or
  Creative Commons) from Wikimedia Commons (`photos.py`), and renders the
  required photographer credit on the card.
- If the player has no free-licensed photo, it falls back to a **photo-free
  design card** (destination team's colors + text).

Either way there are **no copyrighted press photos, no team logos, and no
fabricated reporter names** — so nothing on the card can get the account struck
or demonetized. Copyrighted action shots (Getty/ESPN/NBAE) are never used.

## Running it 24/7 in the cloud (free, no PC) — GitHub Actions

The repo ships a scheduled workflow (`.github/workflows/bot.yml`) that runs the
bot every ~10 minutes on GitHub's servers. Dedup state lives in Upstash Redis
(shared with the dashboard) so runs pick up where the last left off.

**Setup:**
1. Push this repo to GitHub (already done if you followed above).
2. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, and add:
   - `ANTHROPIC_API_KEY`
   - `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`
   - `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`
     (from the Upstash console, or Vercel's `KV_REST_API_URL` / `KV_REST_API_TOKEN`)
3. **Actions** tab → enable workflows → open **NBA news bot** → **Run workflow**
   to test immediately (or wait for the next 10-minute tick).

> Note: Vercel's free cron only fires once/day, too slow for breaking news —
> that's why the bot runs on GitHub Actions instead. The Vercel side is just
> the dashboard.

## Files

| File | Purpose |
|---|---|
| `bot.py` | Entry point. `python bot.py` (loop) or `--once` (single cron pass) |
| `sources.py` | RSS fetching and normalization |
| `composer.py` | Claude API call: classify, write the tweet, extract trade info |
| `card.py` | Generates the TRADE ALERT graphic (Pillow) |
| `photos.py` | Fetches a CC/public-domain player photo + credit from Wikimedia |
| `tweeter.py` | X API posting with optional image (tweepy), dry-run support |
| `state.py` | Dedup + daily counter — Upstash Redis, or local file fallback |
| `config.py` | Settings and feed list |
| `.github/workflows/bot.yml` | Scheduled cloud runner (every ~10 min) |
