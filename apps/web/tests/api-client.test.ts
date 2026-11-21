import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../lib/api/client.ts";
import { ApiError } from "../lib/api/errors.ts";
import { createItem, listItems } from "../lib/api/items.ts";
import { register } from "../lib/api/auth.ts";
import { serverPhotoStore } from "../lib/api/photos.ts";
import { fakeFetch, json } from "./fake-fetch.ts";

function tokens(initial: string | null, fresh: string | null = null) {
  let current = initial;
  let refreshes = 0;
  return {
    get: () => current,
    refresh: async () => {
      refreshes += 1;
      current = fresh;
      return fresh;
    },
    get refreshes() {
      return refreshes;
    },
  };
}

const BASE = "https://api.dolab.test/v1";

test("sends the access token and reads the total count", async () => {
  const f = fakeFetch([() => json(200, [{ id: "a" }], { "X-Total-Count": "7" })]);
  const api = createApiClient({ baseUrl: BASE, tokens: tokens("t1"), fetch: f.fn });
  const page = await listItems(api, { status: "laundry" });
  assert.equal(page.total, 7);
  assert.equal(f.sent[0].headers.get("Authorization"), "Bearer t1");
  assert.equal(f.sent[0].url, `${BASE}/items?status=laundry&limit=50&offset=0`);
});

test("a missing count header is not read as zero", async () => {
  const f = fakeFetch([() => json(200, [{ id: "a" }, { id: "b" }])]);
  const api = createApiClient({ baseUrl: BASE, tokens: tokens("t"), fetch: f.fn });
  assert.equal((await listItems(api)).total, 2);
});

test("leaves out empty filters", async () => {
  const f = fakeFetch([() => json(200, [])]);
  const api = createApiClient({ baseUrl: BASE + "/", tokens: tokens("t"), fetch: f.fn });
  await listItems(api);
  assert.equal(f.sent[0].url, `${BASE}/items?limit=50&offset=0`);
});

test("a 401 refreshes once and repeats the request with the new token", async () => {
  const t = tokens("old", "new");
  const f = fakeFetch([() => json(401, { detail: "expired" }), () => json(201, { id: "i1" })]);
  const api = createApiClient({ baseUrl: BASE, tokens: t, fetch: f.fn });
  const item = await createItem(api, { name: "Shirt", category: "shirt" });
  assert.equal(item.id, "i1");
  assert.equal(t.refreshes, 1);
  assert.equal(f.sent[1].headers.get("Authorization"), "Bearer new");
  assert.deepEqual(f.sent[1].body, { name: "Shirt", category: "shirt" });
});

test("a 401 with no session left becomes an ApiError 401", async () => {
  const f = fakeFetch([() => json(401, { detail: "expired" })]);
  const api = createApiClient({ baseUrl: BASE, tokens: tokens("old", null), fetch: f.fn });
  await assert.rejects(() => listItems(api), (e: unknown) => e instanceof ApiError && e.status === 401);
  assert.equal(f.sent.length, 1);
});

test("requests without auth send no token and never refresh", async () => {
  const t = tokens("secret");
  const f = fakeFetch([() => json(409, { detail: "email already registered" })]);
  const api = createApiClient({ baseUrl: BASE, tokens: t, fetch: f.fn });
  await assert.rejects(
    () => register(api, { email: "a@b.co", password: "12345678", locale: "ar", timezone: "UTC" }),
    (e: unknown) => e instanceof ApiError && e.status === 409 && e.detail === "email already registered",
  );
  assert.equal(f.sent[0].headers.get("Authorization"), null);
  assert.equal(t.refreshes, 0);
});

test("validation errors and Retry-After are kept", async () => {
  const f = fakeFetch([
    () => json(422, { detail: [{ msg: "too short" }, { msg: "bad date" }] }),
    () => json(429, { detail: "slow down" }, { "Retry-After": "900" }),
  ]);
  const api = createApiClient({ baseUrl: BASE, tokens: tokens("t"), fetch: f.fn });
  await assert.rejects(() => listItems(api), (e: unknown) => e instanceof ApiError && e.detail === "too short; bad date");
  await assert.rejects(() => listItems(api), (e: unknown) => e instanceof ApiError && e.retryAfter === 900);
});

test("a network failure is an ApiError with status 0", async () => {
  const api = createApiClient({
    baseUrl: BASE,
    tokens: tokens("t"),
    fetch: (async () => {
      throw new TypeError("failed");
    }) as typeof fetch,
  });
  await assert.rejects(() => listItems(api), (e: unknown) => e instanceof ApiError && e.status === 0);
});

test("signed media paths resolve against the API's origin", () => {
  const api = createApiClient({ baseUrl: BASE, tokens: tokens(null) });
  assert.equal(api.mediaUrl("/v1/media/p1/thumb?exp=1&sig=x"), "https://api.dolab.test/v1/media/p1/thumb?exp=1&sig=x");
});

test("photo upload sends multipart form data, not JSON", async () => {
  const f = fakeFetch([(sent) => {
    assert.ok(sent.body instanceof FormData);
    assert.equal(sent.headers.get("Content-Type"), null);
    return json(201, { id: "p1" });
  }]);
  const store = serverPhotoStore(createApiClient({ baseUrl: BASE, tokens: tokens("t"), fetch: f.fn }));
  const photo = await store.upload("item 1", new Blob(["x"], { type: "image/jpeg" }), "a.jpg");
  assert.equal(photo.id, "p1");
  assert.equal(f.sent[0].url, `${BASE}/items/item%201/photos`);
});
