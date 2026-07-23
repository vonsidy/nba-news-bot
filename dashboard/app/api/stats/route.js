import { redis } from "../../../lib/redis";
import { K } from "../../../lib/x";

// Public, read-only X account stats.
//
// Why this route exists: the X sync writes x:user / x:tweets to THIS app's
// Redis (the Upstash store wired into Vercel). The shared multi-bot dashboard,
// and the bot's own hourly publisher, read a DIFFERENT Redis — the one the
// GitHub Actions bot uses for dedup and budget. So the account totals the sync
// pulled never reached the page that shows them: it published zeros while the
// real numbers sat here.
//
// Rather than reconcile the two databases (which would move the bot's dedup
// state and risk reposts), the shared dashboard fetches this endpoint for the
// account block and keeps reading the committed JSON for everything else. No
// credentials move, the bot is untouched.

export const dynamic = "force-dynamic";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "public, max-age=0, must-revalidate",
};

export async function GET() {
  if (!redis) {
    return Response.json({ account: null, error: "no-redis" }, { headers: CORS });
  }
  const [user, tweets, history, lastSync] = await Promise.all([
    redis.get(K.user),
    redis.get(K.tweets),
    redis.get(K.history),
    redis.get(K.lastSync),
  ]);
  const t = tweets || [];
  const m = (x, k) => ((x.metrics || {})[k] || 0);
  const imp = t.reduce((s, x) => s + m(x, "impressions"), 0);
  const likes = t.reduce((s, x) => s + m(x, "likes"), 0);
  const eng = t.reduce(
    (s, x) => s + m(x, "likes") + m(x, "retweets") + m(x, "replies") + m(x, "quotes"),
    0
  );
  const n = t.length;
  const account = user
    ? {
        followers: user.followers || 0,
        posts: user.tweet_count || n,
        impressions: imp,
        likes,
        avg_impressions: n ? Math.round(imp / n) : 0,
        engagement_rate: imp ? Math.round((eng / imp) * 1e4) / 1e4 : 0,
      }
    : null;

  return Response.json(
    { account, history: history || [], lastSync: lastSync || null },
    { headers: CORS }
  );
}

export function OPTIONS() {
  return new Response(null, {
    headers: { ...CORS, "Access-Control-Allow-Methods": "GET, OPTIONS" },
  });
}
