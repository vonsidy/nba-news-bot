import { syncStats } from "../../../../lib/x";

// Daily snapshot, triggered by Vercel Cron (see vercel.json).
// Vercel sends "Authorization: Bearer <CRON_SECRET>" when CRON_SECRET is set.
export async function GET(req) {
  const secret = process.env.CRON_SECRET;
  if (secret && req.headers.get("authorization") !== `Bearer ${secret}`) {
    return new Response("Unauthorized", { status: 401 });
  }
  try {
    const result = await syncStats();
    return Response.json({ ok: true, tweets: result.tweetCount });
  } catch (e) {
    return Response.json({ ok: false, error: e.message }, { status: 500 });
  }
}
