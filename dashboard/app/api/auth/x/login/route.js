import crypto from "crypto";
import { cookies } from "next/headers";

export async function GET(req) {
  if (!process.env.X_CLIENT_ID) {
    return new Response("X_CLIENT_ID env var is not set in Vercel.", { status: 500 });
  }
  const origin = process.env.APP_URL || new URL(req.url).origin;

  // PKCE: verifier stays in an httpOnly cookie, challenge goes to X
  const verifier = crypto.randomBytes(32).toString("base64url");
  const challenge = crypto.createHash("sha256").update(verifier).digest("base64url");
  const state = crypto.randomBytes(16).toString("hex");

  const jar = await cookies();
  jar.set("x_oauth", JSON.stringify({ verifier, state }), {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    maxAge: 600,
    path: "/",
  });

  const params = new URLSearchParams({
    response_type: "code",
    client_id: process.env.X_CLIENT_ID,
    redirect_uri: `${origin}/api/auth/x/callback`,
    // Read-only scopes on purpose: the dashboard can never post.
    scope: "tweet.read users.read offline.access",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });

  return Response.redirect(`https://x.com/i/oauth2/authorize?${params}`);
}
