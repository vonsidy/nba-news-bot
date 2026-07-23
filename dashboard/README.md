# NBA Bot Dashboard (Vercel)

## The daily X snapshot

`/api/cron/snapshot` is the only thing in this system that READS from X, and
reads are billed per resource returned. Its OAuth 2.0 client secret was
regenerated on 2026-07-21 and deliberately not redeployed, so the sync could
no longer authenticate; the schedule was dropped to stop it retrying daily.

The schedule is now back (2026-07-23, owner's call — the account stats had
been frozen for two days and the shared dashboard was showing them as live).
**It will keep failing until the OAuth credentials exist**, which costs one
failed invocation a day and nothing else.

BOTH variables are missing from the Vercel project, not just the secret —
checked 2026-07-23, the project holds only APP_URL, CRON_SECRET and the KV /
Upstash keys. Setting the secret alone will not work; the login route needs
the id to start the flow at all.

1. developer.x.com → your app → **Keys and tokens** → *OAuth 2.0 Client ID
   and Client Secret*. Copy the id; regenerate the secret and copy it
   immediately, X shows it once.
2. Vercel → `nba-news-bot` → Settings → Environment Variables → add
   **`X_CLIENT_ID`** and **`X_CLIENT_SECRET`**, both scoped to Production.
3. Deployments → newest → ⋯ → **Redeploy**. Env vars only bind on a new build,
   so skipping this makes step 4 fail with a confusing error.
4. Open the dashboard and connect X (the OAuth flow writes the token the
   snapshot uses).

If step 4 fails with a vague OAuth error, it is almost always the callback:
in the X portal under *User authentication settings*, the redirect URI must
match `APP_URL` + `/api/auth/x/callback` exactly — scheme included, no
trailing slash.

To watch it work without waiting for 13:00 UTC: Vercel → the project → Cron
Jobs → Run.

Once those land, the cron writes `x:user` / `x:tweets` / `x:history` to the
shared Upstash Redis, `dashboard_data.publish()` picks them up on its next
hourly run, and the "as of Nd ago" labels on the shared dashboard disappear on
their own — nothing further to change.

Be aware this re-enables the read cost that switching it off was meant to
avoid. Delete the `crons` block to stop it again.

> This note lives here rather than in `vercel.json` because that file is
> schema-validated: a `$comment` key fails the build with *"should NOT have
> additional property `$comment`"*, and it did — every deployment from
> 2026-07-21 e048e2c onward errored until it was removed. JSON has no comments;
> put the reasoning in Markdown.


Web dashboard for the NBA news bot: connect your X account with one click,
see engagement stats, and get data-driven "when to post / how much to post"
recommendations.

## Deploy (one-time, ~10 minutes)

### 1. Push this repo to GitHub
Already done if you followed the main README.

### 2. Import to Vercel
- vercel.com → **Add New → Project** → import your GitHub repo
- Set **Root Directory** to `dashboard`
- Deploy (it will build but show a config warning until step 3-4 are done)
- Note your URL, e.g. `https://nba-bot-dashboard.vercel.app`

### 3. Add Redis storage (free)
- In the Vercel project → **Storage** tab → **Create Database** → Upstash Redis
- This auto-adds the `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` env vars

### 4. Create the X OAuth app
- developer.x.com → your app → **User authentication settings** → Set up
  - App permissions: **Read** (the dashboard never posts)
  - Type of App: **Web App**
  - Callback URI: `https://YOUR-APP.vercel.app/api/auth/x/callback`
  - Website URL: your Vercel URL
- Copy the **OAuth 2.0 Client ID and Client Secret**

### 5. Set environment variables (Vercel → Settings → Environment Variables)

| Variable | Value |
|---|---|
| `X_CLIENT_ID` | from step 4 |
| `X_CLIENT_SECRET` | from step 4 |
| `APP_URL` | your Vercel URL, no trailing slash |
| `CRON_SECRET` | any long random string (protects the daily sync endpoint) |
| `DASHBOARD_PASSWORD` | optional — set to require a password on the site |

Redeploy after adding env vars.

### 6. Connect
Open the site → **Connect X account** → authorize. Stats appear immediately;
a daily cron keeps them fresh.

## How the recommendations work

- **Best posting windows**: your measured engagement rate per hour (ET),
  blended with known NBA-audience patterns (evening game windows dominate).
  With little data it shows the NBA baseline; as posts accumulate, your own
  numbers take over (up to 75% weight).
- **Volume advice**: keyed to your average engagement rate — ~1% is the healthy
  baseline; 2%+ means post more; below ~0.8% means cut low-signal posts.
- **API budget**: the X free tier allows ~100 reads/month. The daily cron uses
  ~60; the Refresh button uses 2 per click. Don't spam refresh.
