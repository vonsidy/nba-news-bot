import { requireRedis } from "../../../../../lib/redis";
import { K } from "../../../../../lib/x";

export async function POST(req) {
  const origin = process.env.APP_URL || new URL(req.url).origin;
  const redis = requireRedis();
  await redis.del(K.tokens, K.user, K.tweets, K.lastSync);
  return Response.redirect(`${origin}/`, 303);
}
