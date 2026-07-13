# NBA Bot Dashboard (Vercel)

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
