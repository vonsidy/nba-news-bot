import { requireRedis } from "./redis";

const TOKEN_URL = "https://api.x.com/2/oauth2/token";
const API = "https://api.x.com/2";

// Redis keys
export const K = {
  tokens: "x:tokens",     // {access_token, refresh_token, expires_at}
  user: "x:user",         // {id, username, name, followers, following, tweet_count}
  tweets: "x:tweets",     // [{id, text, created_at, metrics}]
  history: "x:history",   // [{date, followers, impressions, engagements}] daily snapshots
  lastSync: "x:last_sync",
};

function basicAuth() {
  const creds = `${process.env.X_CLIENT_ID}:${process.env.X_CLIENT_SECRET}`;
  return "Basic " + Buffer.from(creds).toString("base64");
}

export async function exchangeCode(code, verifier, redirectUri) {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: basicAuth(),
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
      client_id: process.env.X_CLIENT_ID,
    }),
  });
  if (!res.ok) throw new Error(`Token exchange failed: ${res.status} ${await res.text()}`);
  return res.json();
}

export async function saveTokens(data) {
  const redis = requireRedis();
  await redis.set(K.tokens, {
    access_token: data.access_token,
    refresh_token: data.refresh_token,
    expires_at: Date.now() + (data.expires_in - 120) * 1000,
  });
}

export async function getValidToken() {
  const redis = requireRedis();
  const tokens = await redis.get(K.tokens);
  if (!tokens) return null;
  if (Date.now() < tokens.expires_at) return tokens.access_token;

  // Access tokens live ~2h; refresh with the offline.access refresh token
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: basicAuth(),
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: tokens.refresh_token,
      client_id: process.env.X_CLIENT_ID,
    }),
  });
  if (!res.ok) return null; // refresh token revoked/expired — user must reconnect
  const data = await res.json();
  await saveTokens(data);
  return data.access_token;
}

async function xGet(path, token) {
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`X API ${res.status} on ${path}: ${await res.text()}`);
  return res.json();
}

/**
 * Pull profile + recent tweet metrics and store them.
 * Costs 2 API reads — the X free tier allows ~100 reads/month, so this runs
 * once daily via cron plus occasional manual refreshes. Don't spam it.
 */
export async function syncStats() {
  const redis = requireRedis();
  const token = await getValidToken();
  if (!token) throw new Error("X account not connected (or session expired) — reconnect on the dashboard.");

  const me = await xGet("/users/me?user.fields=public_metrics", token);
  const user = {
    id: me.data.id,
    username: me.data.username,
    name: me.data.name,
    followers: me.data.public_metrics.followers_count,
    following: me.data.public_metrics.following_count,
    tweet_count: me.data.public_metrics.tweet_count,
  };
  await redis.set(K.user, user);

  const tl = await xGet(
    `/users/${user.id}/tweets?max_results=100&exclude=retweets` +
    `&tweet.fields=public_metrics,created_at`,
    token
  );
  const tweets = (tl.data || []).map((t) => ({
    id: t.id,
    text: t.text,
    created_at: t.created_at,
    metrics: {
      impressions: t.public_metrics.impression_count ?? 0,
      likes: t.public_metrics.like_count,
      retweets: t.public_metrics.retweet_count,
      replies: t.public_metrics.reply_count,
      quotes: t.public_metrics.quote_count,
    },
  }));
  await redis.set(K.tweets, tweets);

  // Append a daily history point (keep ~90 days)
  const totals = tweets.reduce(
    (a, t) => {
      a.impressions += t.metrics.impressions;
      a.engagements += t.metrics.likes + t.metrics.retweets + t.metrics.replies + t.metrics.quotes;
      return a;
    },
    { impressions: 0, engagements: 0 }
  );
  const today = new Date().toISOString().slice(0, 10);
  const history = ((await redis.get(K.history)) || []).filter((h) => h.date !== today);
  history.push({ date: today, followers: user.followers, ...totals });
  await redis.set(K.history, history.slice(-90));
  await redis.set(K.lastSync, Date.now());

  return { user, tweetCount: tweets.length };
}
