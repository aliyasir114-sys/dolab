import { test } from "node:test";
import assert from "node:assert/strict";
import { createSession } from "../lib/session/browser.ts";
import { forwardedChain, handleSession, isSameOrigin, refreshCookieOptions, type SessionRequest } from "../lib/session/server.ts";
import { ApiError } from "../lib/api/errors.ts";
import { fakeFetch, json } from "./fake-fetch.ts";

const API = "https://api.dolab.test/v1";
const SELF = "https://dolab.test";

function request(extra: Partial<SessionRequest> = {}): SessionRequest {
  return { method: "POST", origin: SELF, fetchSite: "same-origin", selfOrigin: SELF, forwardedFor: null,
    refreshToken: null, authorization: null, json: null, ...extra };
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

test("without a proxy in front of the site, the browser's forwarded header is never passed on", () => {
  assert.equal(forwardedChain("198.51.100.7", false), null);
  assert.equal(forwardedChain(null, true), null);
  assert.equal(forwardedChain(" , ", true), null);
});

test("behind a proxy, the chain is passed on in place: last 8 entries, odd ones replaced", () => {
  // The browser wrote "1.1.1.1"; the proxy appended the visitor's real address.
  assert.equal(forwardedChain("1.1.1.1, 198.51.100.7", true), "1.1.1.1, 198.51.100.7");
  assert.equal(forwardedChain("<script>, 198.51.100.7", true), "unknown, 198.51.100.7");
  assert.equal(forwardedChain("[2001:db8::1]:443,198.51.100.7:5000", true), "[2001:db8::1]:443, 198.51.100.7:5000");
  const long = Array.from({ length: 20 }, (_, i) => `10.0.0.${i}`).join(", ");
  assert.equal(forwardedChain(long, true)?.split(", ").length, 8);
  assert.ok(forwardedChain(long, true)?.endsWith("10.0.0.19"));
});

test("only sign-in sends the forwarded chain to the API", async () => {
  const f = fakeFetch([() => json(401, { detail: "wrong email or password" })]);
  await handleSession("login", request({ forwardedFor: "198.51.100.7", json: { email: "a@b.co", password: "x" } }), API, f.fn);
  assert.equal(f.sent[0].headers.get("X-Forwarded-For"), "198.51.100.7");
  const g = fakeFetch([() => json(401, { detail: "wrong email or password" })]);
  await handleSession("login", request({ json: { email: "a@b.co", password: "x" } }), API, g.fn);
  assert.equal(g.sent[0].headers.get("X-Forwarded-For"), null);
  const h = fakeFetch([() => json(200, { access_token: "A", refresh_token: "R", expires_in: 900 })]);
  await handleSession("refresh", request({ forwardedFor: "198.51.100.7", refreshToken: "R0" }), API, h.fn);
  assert.equal(h.sent[0].headers.get("X-Forwarded-For"), null);
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

test("password change: forwards the access token, keeps the new refresh token in the cookie", async () => {
  const f = fakeFetch([() => json(200, { access_token: "A2", refresh_token: "R2", expires_in: 900 })]);
  const result = await handleSession("password",
    request({ authorization: "Bearer A1", json: { current_password: "old-pass", new_password: "new-password" } }), API, f.fn);
  assert.equal(f.sent[0].url, `${API}/me/password`);
  assert.equal(f.sent[0].headers.get("Authorization"), "Bearer A1");
  assert.deepEqual(result.body, { access_token: "A2", expires_in: 900 });
  assert.equal(result.cookie, "R2");
});

test("password change needs a bearer token and both fields, and passes refusals through", async () => {
  const none = await handleSession("password", request({ json: { current_password: "a", new_password: "b" } }), API, fakeFetch([]).fn);
  assert.equal(none.status, 401);
  const half = await handleSession("password", request({ authorization: "Bearer A", json: { new_password: "b" } }), API, fakeFetch([]).fn);
  assert.equal(half.status, 422);
  const f = fakeFetch([() => json(403, { detail: "current password is wrong" })]);
  const wrong = await handleSession("password",
    request({ authorization: "Bearer A", json: { current_password: "x", new_password: "new-password" } }), API, f.fn);
  assert.equal(wrong.status, 403);
  assert.equal(wrong.cookie, undefined);
});

test("browser password change retries once after refreshing an expired access token", async () => {
  const f = fakeFetch([
    () => json(200, { access_token: "A1", expires_in: 900 }),          // sign in
    () => json(401, { detail: "token expired" }),                     // password with A1
    () => json(200, { access_token: "A2", expires_in: 900 }),          // refresh
    () => json(200, { access_token: "A3", expires_in: 900 }),          // password with A2
  ]);
  const session = createSession({ fetch: f.fn });
  await session.signIn("a@b.co", "pw");
  await session.changePassword("pw", "new-password");
  assert.equal(f.sent[1].headers.get("Authorization"), "Bearer A1");
  assert.equal(f.sent[3].headers.get("Authorization"), "Bearer A2");
  assert.equal(session.get(), "A3");
});
