/**
 * Step C-1 across the site and the API: the sign-in limit sees the visitor's address, not the
 * web server's (reviewer's pre-H task (a)). CI plays the reverse proxy: it sends the
 * X-Forwarded-For a proxy would append, the site runs with DOLAB_WEB_BEHIND_PROXY=1, and the API
 * trusts only the web server's address (DOLAB_TRUSTED_PROXIES=127.0.0.1/32,::1/128).
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { request, type APIRequestContext } from "playwright";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/v1";
const EMAIL = `e2e-address-${Date.now()}@example.com`;
const PASSWORD = "a-long-test-password";

let web: APIRequestContext;

before(async () => {
  web = await request.newContext({ baseURL: WEB });
  const api = await request.newContext();
  const registered = await api.post(`${API}/auth/register`, { data: { email: EMAIL, password: PASSWORD } });
  assert.equal(registered.status(), 201, await registered.text());
  await api.dispose();
});

after(async () => {
  await web?.dispose();
});

async function signIn(password: string, forwardedFor: string) {
  const response = await web.post("/api/session/login", {
    headers: { Origin: WEB, "Sec-Fetch-Site": "same-origin", "X-Forwarded-For": forwardedFor },
    data: { email: EMAIL, password },
  });
  return response.status();
}

test("five wrong passwords block that visitor, not every visitor of the site", async () => {
  for (let n = 0; n < 5; n++) assert.equal(await signIn("wrong-password", "203.0.113.10"), 401);
  assert.equal(await signIn(PASSWORD, "203.0.113.10"), 429);
  // Another visitor, through the same web server, still signs in.
  assert.equal(await signIn(PASSWORD, "198.51.100.20"), 200);
});

test("an address the browser writes on the left is never used", async () => {
  // The blocked visitor claims to be someone else; the proxy's entry on the right still names them.
  assert.equal(await signIn(PASSWORD, "198.51.100.99, 203.0.113.10"), 429);
});
