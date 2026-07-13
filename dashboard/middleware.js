import { NextResponse } from "next/server";

// Optional protection: set DASHBOARD_PASSWORD in Vercel and the whole site
// asks for it (username: admin). Leave unset to keep the dashboard open.
// The cron route is excluded — it authenticates with CRON_SECRET instead.
export function middleware(req) {
  const pw = process.env.DASHBOARD_PASSWORD;
  if (!pw) return NextResponse.next();

  const auth = req.headers.get("authorization") || "";
  const expected = "Basic " + Buffer.from(`admin:${pw}`).toString("base64");
  if (auth === expected) return NextResponse.next();

  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="dashboard"' },
  });
}

export const config = {
  matcher: ["/((?!api/cron|_next|favicon.ico).*)"],
};
