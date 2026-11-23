/**
 * The logic behind the web's session routes (app/api/session/[action]/route.ts), kept free of
 * Next.js so it can be tested on its own. Those routes run on the Node.js runtime and set the
 * cookie with cookies().set(): Next.js advisory GHSA-f3mh-rxg3-f5g2 (Nov 2026) showed Edge routes
 * using NextResponse.cookies.set() exposing HttpOnly cookie values to page scripts.
 */
export const REFRESH_COOKIE = "__Host-dolab_rt";
const THIRTY_DAYS = 30 * 24 * 60 * 60;

export type CookieOptions = {
  httpOnly: true;
  secure: true;
  sameSite: "strict";
  path: "/";
  maxAge: number;
};

/** __Host- prefix: the browser accepts it only with Secure, Path=/ and no Domain. */
export function refreshCookieOptions(maxAge: number = THIRTY_DAYS): CookieOptions {
  return { httpOnly: true, secure: true, sameSite: "strict", path: "/", maxAge };
}

export type SessionResult = {
  status: number;
  /** JSON for the page. Never contains the refresh token. */
  body: Record<string, unknown> | null;
  headers?: Record<string, string>;
  /** Set the refresh cookie to this value, clear it (null), or leave it (undefined). */
  cookie?: string | null;
};

export type SessionRequest = {
  method: string;
  /** Origin and Sec-Fetch-Site headers of the incoming request. */
  origin: string | null;
  fetchSite: string | null;
  /** The web app's own origin, e.g. https://dolab.example */
  selfOrigin: string;
  /** X-Forwarded-For to pass to the API with a sign-in, from forwardedChain(): the addresses
   *  written by the trusted proxy in front of this site. null when this site has no such proxy. */
  forwardedFor: string | null;
  refreshToken: string | null;
  /** The page's "Authorization: Bearer <access token>", needed by the password action. */
  authorization: string | null;
  json: unknown;
};

/** password: the API's POST /me/password answers with a new token pair (and signs out every other
 *  session), so it goes through here like sign-in: the new refresh token goes into the cookie and
 *  never reaches the page. */
export const ACTIONS = ["login", "refresh", "logout", "password"] as const;
export type Action = (typeof ACTIONS)[number];

/** Only this site's own pages may call the session routes (CSRF), on top of SameSite=Strict. */
export function isSameOrigin(req: Pick<SessionRequest, "origin" | "fetchSite" | "selfOrigin">): boolean {
  if (req.fetchSite !== null && req.fetchSite !== "same-origin") return false;
  return req.origin !== null && req.origin === req.selfOrigin;
}

/** At most this many addresses are passed on: the API reads from the right, so the left is noise. */
const MAX_FORWARDED = 8;
const ADDRESS_LIKE = /^[0-9A-Za-z.:[\]_-]{1,64}$/;

/**
 * The X-Forwarded-For chain to pass to the API with a sign-in, so its per-address limit sees the
 * visitor and not this server (plan 4.7; reviewer's pre-H task (a)).
 *
 * Only when this site runs behind a reverse proxy that appends the address it received from
 * (DOLAB_WEB_BEHIND_PROXY=1): then the rightmost entry is the proxy's, whatever the browser wrote
 * further left. The API believes the header only from addresses in its DOLAB_TRUSTED_PROXIES,
 * which must list this server (and any load balancer), and reads it from the right, so the
 * browser's part is never reached. Without such a proxy the header would be the browser's own
 * words, so nothing is passed (the default).
 *
 * Entries are kept in place (only the last 8); anything that does not look like an address is
 * replaced by "unknown", so the positions the API counts from the right do not move.
 */
export function forwardedChain(header: string | null, behindProxy: boolean): string | null {
  if (!behindProxy || !header) return null;
  const entries = header.split(",").map((part) => part.trim()).filter(Boolean).slice(-MAX_FORWARDED);
  if (!entries.length) return null;
  return entries.map((entry) => (ADDRESS_LIKE.test(entry) ? entry : "unknown")).join(", ");
}

type Fetch = typeof fetch;

async function callApi(doFetch: Fetch, apiUrl: string, path: string, json: unknown, authorization: string | null = null,
  forwardedFor: string | null = null) {
  const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "application/json" };
  if (authorization) headers.Authorization = authorization;
  if (forwardedFor) headers["X-Forwarded-For"] = forwardedFor;
  return doFetch(apiUrl.replace(/\/+$/, "") + path, {
    method: "POST",
    headers,
    body: JSON.stringify(json),
    cache: "no-store",
  });
}

async function passError(response: Response, cookie?: null): Promise<SessionResult> {
  let body: Record<string, unknown> | null = null;
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    body = null;
  }
  const headers: Record<string, string> = {};
  const retry = response.headers.get("Retry-After");
  if (retry) headers["Retry-After"] = retry;
  return { status: response.status, body: { detail: body?.detail ?? "" }, headers, cookie };
}

async function tokens(response: Response): Promise<SessionResult> {
  const data = (await response.json()) as { access_token: string; refresh_token: string; expires_in: number };
  return {
    status: 200,
    body: { access_token: data.access_token, expires_in: data.expires_in },
    cookie: data.refresh_token,
  };
}

export async function handleSession(action: string, req: SessionRequest, apiUrl: string, doFetch: Fetch): Promise<SessionResult> {
  if (req.method !== "POST" || !(ACTIONS as readonly string[]).includes(action)) {
    return { status: 404, body: { detail: "not found" } };
  }
  if (!isSameOrigin(req)) return { status: 403, body: { detail: "cross-site request refused" } };

  try {
    if (action === "login") {
      const input = (req.json ?? {}) as { email?: unknown; password?: unknown };
      if (typeof input.email !== "string" || typeof input.password !== "string") {
        return { status: 422, body: { detail: "email and password are required" } };
      }
      const response = await callApi(doFetch, apiUrl, "/auth/login",
        { email: input.email, password: input.password }, null, req.forwardedFor);
      return response.ok ? await tokens(response) : await passError(response);
    }

    if (action === "refresh") {
      if (!req.refreshToken) return { status: 401, body: { detail: "no session" }, cookie: null };
      const response = await callApi(doFetch, apiUrl, "/auth/refresh",
        { refresh_token: req.refreshToken });
      // A refused refresh ends the session here too: clear the cookie.
      return response.ok ? await tokens(response) : await passError(response, response.status === 401 ? null : undefined);
    }

    if (action === "password") {
      const input = (req.json ?? {}) as { current_password?: unknown; new_password?: unknown };
      if (!req.authorization || !/^Bearer \S+$/.test(req.authorization)) {
        return { status: 401, body: { detail: "not signed in" } };
      }
      if (typeof input.current_password !== "string" || typeof input.new_password !== "string") {
        return { status: 422, body: { detail: "current_password and new_password are required" } };
      }
      const response = await callApi(doFetch, apiUrl, "/me/password",
        { current_password: input.current_password, new_password: input.new_password }, req.authorization);
      return response.ok ? await tokens(response) : await passError(response);
    }

    // logout: tell the API (best effort), and always clear the cookie.
    if (req.refreshToken) {
      await callApi(doFetch, apiUrl, "/auth/logout", { refresh_token: req.refreshToken })
        .catch(() => undefined);
    }
    return { status: 204, body: null, cookie: null };
  } catch {
    return { status: 502, body: { detail: "the DOLAB service could not be reached" } };
  }
}
