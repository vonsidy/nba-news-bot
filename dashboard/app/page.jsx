import { redis } from "../lib/redis";
import { K } from "../lib/x";
import Dashboard from "./dashboard";

export const dynamic = "force-dynamic";

export default async function Page({ searchParams }) {
  const params = await searchParams;

  if (!redis) {
    return (
      <main>
        <h1>TheNBASignal</h1>
        <div className="banner err">
          Redis isn&apos;t configured yet. In Vercel: project → <b>Storage</b> →
          add <b>Upstash Redis</b> (free), then redeploy.
        </div>
      </main>
    );
  }

  const today = new Date().toISOString().slice(0, 10);
  const cap = parseInt(process.env.MAX_POSTS_PER_DAY || "8", 10);
  const [user, tweets, history, lastSync, postsToday, highlightsToday] = await Promise.all([
    redis.get(K.user),
    redis.get(K.tweets),
    redis.get(K.history),
    redis.get(K.lastSync),
    redis.get(`bot:posts:${today}`),
    redis.get(`bot:highlights:${today}`),
  ]);

  return (
    <Dashboard
      user={user || null}
      tweets={tweets || []}
      history={history || []}
      lastSync={lastSync || null}
      botStats={{ postsToday: postsToday ? Number(postsToday) : 0, highlightsToday: highlightsToday ? Number(highlightsToday) : 0, cap }}
      connected={!!user}
      params={params || {}}
    />
  );
}
