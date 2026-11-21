import { test } from "node:test";
import assert from "node:assert/strict";
import { createSession } from "../lib/session/browser.ts";
import { handleSession, isSameOrigin, refreshCookieOptions, type SessionRequest } from "../lib/session/server.ts";
import { ApiError } from "../lib/api/errors.ts";
import { fakeFetch, json } from "./fake-fetch.ts";

const API = "https://api.dolab.test/v1";
const SELF = "https://dolab.test";

function request(extra: Partial<SessionRequest> = {}): SessionRequest {
  return { method: "POST", origin: SELF, fetchSite: "same-origin", selfOrigin: SELF, forwardedFor: null,
    refreshToken: null, json: null, ...extra };
}

// ---- server side

test("login returns the access token only; the refresh token goes to the cookie", async () => {
  const f = fakeFetch([() => json(200, { access_token: "A", refresh_token: "R", token_type: "bearer", expires_in: 900 })]);
  const result = await handleSession("login", request({ json: { email: "a@b.co", password: "pw" } }), API, f.fn);
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { access_token: "A", expires_in: 900 });
  assert.equal(result.cookie, "R");
  assert.ok(!JSON.stringify(result.body).includes("R\""));
  assert.equal(f.sent[0].url, `${API}/auth/login`);
});

test("the visitor's forwarded address reaches the API (sign-in limits per address)", async () => {
  const f = fakeFetch([() => json(401, { detail: "wrong email or password" })]);
  await handleSession("login", request({ forwardedFor: "198.51.100.7", json: { email: "a@b.co", password: "x" } }), API, f.fn);
  assert.equal(f.sent[0].headers.get("X-Forwarded-For"), "198.51.100.7");
});

test("API errors pass through with Retry-After, and leave the cookie alone", async () => {
  const f = fakeFetch([() => json(429, { detail: "too many attempts" }, { "Retry-After": "900" })]);
  const result = await handleSession("login", request({ json: { email: "a@b.co", password: "x" } }), API, f.fn);
  assert.equal(result.status, 429);
  assert.equal(result.headers?.["Retry-After"], "900");
  assert.equal(result.cookie, undefined);
});

test("refresh uses the cookie and rotates it", async () => {
  const f = fakeFetch([() => json(200, { access_token: "A2", refresh_token: "R2", expires_in: 900 })]);
  const result = await handleSession("refresh", request({ refreshToken: "R1" }), API, f.fn);
  assert.deepEqual(f.sent[0].body, { refresh_token: "R1" });
  assert.equal(result.cookie, "R2");
});

test("a refused refresh clears the cookie; no cookie means no call", async () => {
  const f = fakeFetch([() => json(401, { detail: "session expired, sign in again" })]);
  assert.equal((await handleSession("refresh", request({ refreshToken: "old" }), API, f.fn)).cookie, null);
  const none = await handleSession("refresh", request(), API, fakeFetch([]).fn);
  assert.equal(none.status, 401);
  assert.equal(none.cookie, null);
});

test("logout tells the API and clears the cookie even if the API is down", async () => {
  const f = fakeFetch([() => {
    throw new TypeError("down");
  }]);
  const result = await handleSession("logout", request({ refreshToken: "R" }), API, f.fn);
  assert.equal(result.status, 204);
  assert.equal(result.cookie, null);
});

test("cross-site requests are refused before any API call", async () => {
  for (const extra of [{ origin: "https://evil.test" }, { origin: null }, { fetchSite: "cross-site" }, { fetchSite: "same-site" }]) {
    const result = await handleSession("login", request({ ...extra, json: { email: "a", password: "b" } }), API, fakeFetch([]).fn);
    assert.equal(result.status, 403, JSON.stringify(extra));
  }
  assert.equal(isSameOrigin({ origin: SELF, fetchSite: null, selfOrigin: SELF }), true);
});

test("only POST and the three actions exist", async () => {
  assert.equal((await handleSession("login", request({ method: "GET" }), API, fakeFetch([]).fn)).status, 404);
  assert.equal((await handleSession("tokens", request(), API, fakeFetch([]).fn)).status, 404);
});

test("an unreachable API is a 502, not a crash", async () => {
  const f = fakeFetch([() => {
    throw new TypeError("down");
  }]);
  const result = await handleSession("login", request({ json: { email: "a@b.co", password: "x" } }), API, f.fn);
  assert.equal(result.status, 502);
});

test("the refresh cookie is HttpOnly, Secure, SameSite=Strict, host-only for 30 days", () => {
  assert.deepEqual(refreshCookieOptions(), { httpOnly: true, secure: true, sameSite: "strict", path: "/", maxAge: 2592000 });
});

// ---- browser side

test("sign-in keeps the access token in memory only", async () => {
  const f = fakeFetch([() => json(200, { access_token: "A", expires_in: 900 })]);
  const session = createSession({ fetch: f.fn });
  await session.signIn("a@b.co", "pw");
  assert.equal(session.get(), "A");
  assert.equal(f.sent[0].url, "/api/session/login");
  assert.equal(typeof (globalThis as { localStorage?: unknown }).localStorage, "undefined");
});

test("a failed sign-in throws the server's status", async () => {
  const session = createSession({ fetch: fakeFetch([() => json(401, { detail: "wrong email or password" })]).fn });
  await assert.rejects(() => session.signIn("a@b.co", "x"), (e: unknown) => e instanceof ApiError && e.status === 401);
  assert.equal(session.isSignedIn(), false);
});

test("parallel 401s share one refresh, so the refresh token is used once", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const f = fakeFetch([() => json(200, { access_token: "A2", expires_in: 900 })]);
  const slowFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    await gate;
    return f.fn(input, init);
  }) as typeof fetch;
  const session = createSession({ fetch: slowFetch });
  const all = Promise.all([session.refresh(), session.refresh(), session.refresh()]);
  release();
  assert.deepEqual(await all, ["A2", "A2", "A2"]);
  assert.equal(f.sent.length, 1);
});

test("sign-out forgets the token even when the request fails", async () => {
  const f = fakeFetch([() => json(200, { access_token: "A", expires_in: 900 }), () => {
    throw new TypeError("down");
  }]);
  const session = createSession({ fetch: f.fn });
  await session.signIn("a@b.co", "pw");
  await session.signOut();
  assert.equal(session.get(), null);
});
