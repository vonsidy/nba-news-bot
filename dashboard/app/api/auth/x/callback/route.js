import { cookies } from "next/headers";
import { exchangeCode, saveTokens, syncStats } from "../../../../../lib/x";

export async function GET(req) {
  const url = new URL(req.url);
  const origin = process.env.APP_URL || url.origin;
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");

  const jar = await cookies();
  const raw = jar.get("x_oauth")?.value;
  jar.delete("x_oauth");

  if (!code || !raw) {
    return Response.redirect(`${origin}/?error=oauth_missing`);
  }
  const { verifier, state: expectedState } = JSON.parse(raw);
  if (state !== expectedState) {
    return Response.redirect(`${origin}/?error=oauth_state`);
  }

  try {
    const tokens = await exchangeCode(code, verifier, `${origin}/api/auth/x/callback`);
    await saveTokens(tokens);
    await syncStats(); // first snapshot right away so the dashboard isn't empty
  } catch (e) {
    console.error("OAuth callback failed:", e);
    return Response.redirect(`${origin}/?error=${encodeURIComponent(e.message.slice(0, 120))}`);
  }
  return Response.redirect(`${origin}/?connected=1`);
}
