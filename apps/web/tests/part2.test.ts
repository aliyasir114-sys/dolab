import { test } from "node:test";
import assert from "node:assert/strict";
import { canWear, statusTargets } from "../lib/status.ts";
import { daysAgo, daysBetween, formatDay, formatPrice, itemRef, monthsBetween, todayIn } from "../lib/format.ts";
import { createApiClient } from "../lib/api/client.ts";
import { serverLifecycle } from "../lib/api/lifecycle.ts";
import { saveProfile } from "../lib/api/account.ts";
import { wearItem } from "../lib/api/items.ts";
import { fakeFetch, json } from "./fake-fetch.ts";

test("status targets mirror the server's transition table", () => {
  assert.deepEqual(statusTargets("available"), ["laundry", "repair", "stored", "lost"]);
  assert.deepEqual(statusTargets("laundry"), ["available", "repair", "stored", "lost"]);
  assert.deepEqual(statusTargets("lost"), ["available"]);
  assert.deepEqual(statusTargets("donated"), []);
  assert.deepEqual(statusTargets("for_sale"), []);
  assert.equal(canWear("available"), true);
  assert.equal(canWear("laundry"), false);
});

test("day arithmetic", () => {
  assert.equal(daysBetween("2026-11-01", "2026-11-22"), 21);
  assert.equal(monthsBetween("2026-03-23", "2026-11-22"), 7);
  assert.equal(monthsBetween("2026-03-22", "2026-11-22"), 8);
  assert.equal(monthsBetween("2026-12-01", "2026-11-22"), 0);
});

test("relative days, dates and prices in both languages", () => {
  const forms = { today: "today", yesterday: "yesterday",
    days: { zero: "{n} days ago", one: "{n} day ago", two: "{n} days ago", few: "{n} days ago", many: "{n} days ago", other: "{n} days ago" } };
  assert.equal(daysAgo("2026-11-22", "2026-11-22", forms, "en"), "today");
  assert.equal(daysAgo("2026-11-21", "2026-11-22", forms, "en"), "yesterday");
  assert.equal(daysAgo("2026-11-19", "2026-11-22", forms, "en"), "3 days ago");
  assert.equal(formatDay("2026-03-05", "en"), "March 5, 2026");
  assert.match(formatDay("2026-03-05", "ar"), /^5 مارس 2026$/);
  assert.match(formatPrice("45.50", "SDG", "en"), /SDG\s?45\.50/);
  assert.equal(itemRef("2f6c1a9e-0000-4000-8000-000000000000"), "DLB-2F6C1A");
});

const BASE = "https://api.dolab.test/v1";
const tokens = { get: () => "t", refresh: async () => null };

test("data lifecycle: export is a JSON file; deletions call the right endpoints", async () => {
  const f = fakeFetch([
    () => json(200, { user: { email: "a@b.co" }, items: [] }),
    () => json(204, null),
    () => json(204, null),
    () => json(204, null),
  ]);
  const life = serverLifecycle(createApiClient({ baseUrl: BASE, tokens, fetch: f.fn }));
  const file = await life.exportAccount();
  assert.equal(file.type, "application/json");
  assert.deepEqual(JSON.parse(await file.text()).user, { email: "a@b.co" });
  await life.clearProfile();
  await life.deleteItem("i 1");
  await life.deleteAccount();
  assert.deepEqual(f.sent.map((s) => `${s.method} ${s.url.replace(BASE, "")}`),
    ["GET /me/export", "DELETE /me/profile", "DELETE /items/i%201", "DELETE /me"]);
});

test("profile save sends every field; wear sends a day only when given", async () => {
  const f = fakeFetch([() => json(200, {}), () => json(200, {}), () => json(200, {})]);
  const api = createApiClient({ baseUrl: BASE, tokens, fetch: f.fn });
  await saveProfile(api, { height_cm: 180, weight_kg: null, usual_size: "M", body_shape: null, face_shape: null });
  assert.deepEqual(f.sent[0].body, { height_cm: 180, weight_kg: null, usual_size: "M", body_shape: null, face_shape: null });
  await wearItem(api, "x");
  await wearItem(api, "x", "2026-11-20");
  assert.deepEqual(f.sent[1].body, {});
  assert.deepEqual(f.sent[2].body, { worn_on: "2026-11-20" });
});

test("today follows the account's time zone", () => {
  const now = new Date("2026-11-22T22:30:00Z");
  assert.equal(todayIn("Africa/Khartoum", now), "2026-11-23");
  assert.equal(todayIn("America/New_York", now), "2026-11-22");
  assert.equal(todayIn("Not/AZone", now).length, 10);
});
