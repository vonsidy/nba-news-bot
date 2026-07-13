# NBA Breaking News Bot

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

## Files

| File | Purpose |
|---|---|
| `bot.py` | Main loop: poll feeds → compose → post |
| `sources.py` | RSS fetching and normalization |
| `composer.py` | Claude API call: classify + write the tweet |
| `tweeter.py` | X API posting (tweepy), dry-run support |
| `config.py` | Settings and feed list |
| `state.json` | Dedup memory + daily post counter (auto-created) |
