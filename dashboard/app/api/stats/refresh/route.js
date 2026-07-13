import { syncStats } from "../../../../lib/x";

// Manual "Refresh stats" button. Each sync costs 2 X API reads out of ~100/month
// on the free tier — the daily cron uses ~60, so manual refreshes are for
// checking in after a big story, not for hammering.
export async function POST(req) {
  const origin = process.env.APP_URL || new URL(req.url).origin;
  try {
    await syncStats();
  } catch (e) {
    return Response.redirect(`${origin}/?error=${encodeURIComponent(e.message.slice(0, 120))}`, 303);
  }
  return Response.redirect(`${origin}/?refreshed=1`, 303);
}
