import { cookies } from "next/headers";
import { forwardedChain, handleSession, REFRESH_COOKIE, refreshCookieOptions } from "../../../../lib/session/server.ts";

// Node.js runtime and cookies().set(): see lib/session/server.ts (advisory GHSA-f3mh-rxg3-f5g2).
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = process.env.DOLAB_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/v1";
// Set to 1 only when a reverse proxy in front of this site appends the visitor's address to
// X-Forwarded-For (see forwardedChain). Off by default: the header is then never passed on.
const BEHIND_PROXY = process.env.DOLAB_WEB_BEHIND_PROXY === "1";

export async function POST(request: Request, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  const jar = await cookies();
  let json: unknown = null;
  if (action === "login" || action === "password") {
    try {
      json = await request.json();
    } catch {
      json = null;
    }
  }
  const result = await handleSession(
    action,
    {
      method: request.method,
      origin: request.headers.get("origin"),
      fetchSite: request.headers.get("sec-fetch-site"),
      selfOrigin: process.env.DOLAB_WEB_ORIGIN ?? new URL(request.url).origin,
      forwardedFor: action === "login" ? forwardedChain(request.headers.get("x-forwarded-for"), BEHIND_PROXY) : null,
      refreshToken: jar.get(REFRESH_COOKIE)?.value ?? null,
      authorization: request.headers.get("authorization"),
      json,
    },
    API_URL,
    fetch,
  );
  if (result.cookie === null) {
    // Clearing a __Host- cookie needs the same Secure and Path attributes.
    jar.set(REFRESH_COOKIE, "", refreshCookieOptions(0));
  } else if (typeof result.cookie === "string") {
    jar.set(REFRESH_COOKIE, result.cookie, refreshCookieOptions());
  }
  const headers = new Headers({ "Cache-Control": "no-store", ...result.headers });
  if (result.body === null) return new Response(null, { status: result.status, headers });
  headers.set("Content-Type", "application/json");
  return new Response(JSON.stringify(result.body), { status: result.status, headers });
}
