import { test } from "node:test";
import assert from "node:assert/strict";
import { ar, en, fill } from "../lib/i18n/messages.ts";
import { checkPhotos, looksLikeEmail, validateItem, type ItemForm } from "../lib/validation/item.ts";

function keys(value: unknown, prefix = ""): string[] {
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
  }
  return [prefix];
}

test("Arabic and English have the same keys, none empty", () => {
  assert.deepEqual(keys(en).sort(), keys(ar).sort());
  for (const [lang, messages] of [["ar", ar], ["en", en]] as const) {
    const flat = keys(messages).map((k) => k.split(".").reduce<unknown>((o, p) => (o as Record<string, unknown>)[p], messages));
    assert.ok(flat.every((v) => typeof v === "string" && v.trim()), lang);
  }
});

test("English text has no Arabic letters, and the same placeholders as Arabic", () => {
  const arabic = /[؀-ۿ]/;
  const leaves = (m: typeof ar) => keys(m).map((k) => [k, k.split(".").reduce<unknown>((o, p) => (o as Record<string, unknown>)[p], m) as string]);
  const arMap = new Map(leaves(ar));
  for (const [key, text] of leaves(en)) {
    if (key !== "nav.otherLanguage" && key !== "nav.otherLanguageName") assert.ok(!arabic.test(text), key);
    const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    assert.equal(holes(text), holes(arMap.get(key)!), key);
  }
});

test("fill replaces known placeholders only", () => {
  assert.equal(fill("{a} and {b}", { a: 1 }), "1 and {b}");
});

const blank: ItemForm = { name: "", category: "", primaryColor: "", status: "available", purchaseDate: "", price: "", currency: "" };

test("item: name and type are required, and trimmed", () => {
  const { errors } = validateItem(blank, "2026-11-21");
  assert.deepEqual(errors.map((e) => `${e.field}:${e.code}`), ["name:required", "category:required"]);
  const ok = validateItem({ ...blank, name: "  قميص  ", category: " shirt " }, "2026-11-21");
  assert.equal(ok.input?.name, "قميص");
  assert.equal(ok.input?.primary_color, null);
});

test("item: price, currency, date and status follow the server's rules", () => {
  const base = { ...blank, name: "x", category: "y" };
  const codes = (f: Partial<ItemForm>) => validateItem({ ...base, ...f }, "2026-11-21").errors.map((e) => e.code);
  assert.deepEqual(codes({ price: "45.50", currency: "sdg" }), []);
  assert.equal(validateItem({ ...base, price: "45,5", currency: "sdg" }, "2026-11-21").input?.purchase_price, "45.5");
  assert.equal(validateItem({ ...base, price: "45", currency: "sdg" }, "2026-11-21").input?.currency, "SDG");
  assert.deepEqual(codes({ price: "-1" }), ["badPrice"]);
  assert.deepEqual(codes({ price: "1.234" }), ["badPrice"]);
  assert.deepEqual(codes({ price: "12345678901" }), ["badPrice"]);
  assert.deepEqual(codes({ currency: "SD" }), ["badCurrency"]);
  assert.deepEqual(codes({ currency: "SDG" }), ["currencyNeedsPrice"]);
  assert.deepEqual(codes({ purchaseDate: "2026-11-22" }), ["futureDate"]);
  assert.deepEqual(codes({ purchaseDate: "2026-11-21" }), []);
  assert.deepEqual(codes({ status: "donated" }), ["required"]);
  assert.deepEqual(codes({ name: "x".repeat(121) }), ["tooLong"]);
});

test("photos: type, size and count", () => {
  const ok = { name: "a.jpg", type: "image/jpeg", size: 1000 };
  assert.deepEqual(checkPhotos([ok]), []);
  assert.deepEqual(checkPhotos([{ ...ok, type: "image/gif" }]).map((p) => p.code), ["badPhotoType"]);
  assert.deepEqual(checkPhotos([{ ...ok, size: 8 * 1024 * 1024 + 1 }]).map((p) => p.code), ["photoTooBig"]);
  assert.deepEqual(checkPhotos(Array(7).fill(ok)).map((p) => p.code), ["tooManyPhotos"]);
});

test("email check", () => {
  assert.ok(looksLikeEmail(" a@b.co "));
  assert.ok(!looksLikeEmail("a@b"));
});
