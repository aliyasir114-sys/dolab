/**
 * End-to-end check of part 1 against the real API, in Chromium (CI job "e2e").
 * Needs the API on API_ORIGIN (CORS allowing the site) and the built site on WEB_URL.
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import { ar, en } from "../lib/i18n/messages.ts";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const SHOTS = process.env.SHOTS_DIR ?? "e2e/shots";
const EMAIL = `e2e-${Date.now()}@example.com`;
const PASSWORD = "a-long-test-password";

// A small valid PNG (a black block on the palette white), enough for the API's image processing.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAIAAABvFaqvAAAAPUlEQVR4XmP8+vUrAzUAEzUMAYHBZxALpteEhYUJ+vft27doagaf10ZdRBiMhhFhMBpGhAHVwohxtMwmCAAUwAjvo9jZ5AAAAABJRU5ErkJggg==",
  "base64",
);

let browser: Browser;
let context: BrowserContext;
let page: Page;
const problems: string[] = [];

// The browser logs every 4xx response as a console error. These are the ones the scenario
// causes on purpose (a wrong password; a refresh with no session after signing out).
const EXPECTED = [/status of 401/];
// A request cut short because the page moved on (e.g. the session check before a redirect).
const ABORTED = "net::ERR_ABORTED";

before(async () => {
  mkdirSync(SHOTS, { recursive: true });
  browser = await chromium.launch();
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !EXPECTED.some((re) => re.test(m.text()))) problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`script: ${e.message}`));
  page.on("requestfailed", (r) => {
    if (r.failure()?.errorText !== ABORTED) problems.push(`failed: ${r.url()} ${r.failure()?.errorText}`);
  });
});

after(async () => {
  await browser?.close();
});

async function shot(name: string) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function storageIsEmpty(): Promise<boolean> {
  return page.evaluate(async () => {
    const dbs = "databases" in indexedDB ? await indexedDB.databases() : [];
    return localStorage.length === 0 && sessionStorage.length === 0 && dbs.length === 0;
  });
}

async function refreshCookie() {
  return (await context.cookies()).find((c) => c.name === "__Host-dolab_rt");
}

test("sign up, add an item with a photo, see it in the wardrobe (Arabic)", async () => {
  await page.goto(`${WEB}/`);
  await page.waitForURL(/\/ar\/sign-in/);
  await page.getByRole("heading", { level: 1, name: ar.auth.signInTitle }).waitFor();
  assert.equal(await page.getAttribute("html", "dir"), "rtl");
  assert.equal(await page.getAttribute("html", "lang"), "ar");
  await shot("01-sign-in-ar");

  await page.click(`a[href="/ar/sign-up"]`);
  await page.waitForURL(/\/ar\/sign-up$/);
  await page.getByRole("heading", { level: 1, name: ar.auth.signUpTitle }).waitFor();
  await page.fill("#email", EMAIL);
  await page.fill("#password", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(/\/ar\/wardrobe$/);
  await page.getByText(ar.wardrobe.empty).waitFor();
  await shot("02-wardrobe-empty-ar");

  const cookie = await refreshCookie();
  assert.ok(cookie, "refresh cookie set");
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.secure, true);
  assert.equal(cookie.sameSite, "Strict");
  assert.equal(cookie.path, "/");
  assert.equal(await page.evaluate(() => document.cookie), "", "no cookie readable by scripts");
  assert.ok(await storageIsEmpty(), "nothing in localStorage, sessionStorage or IndexedDB (P10)");

  // Validation: an empty form shows errors and focuses the first field.
  await page.click(`a[href="/ar/wardrobe/new"]`);
  await page.waitForURL(/\/ar\/wardrobe\/new$/);
  await page.getByRole("heading", { level: 1, name: ar.item.addTitle }).waitFor();
  await page.click("button[type=submit]");
  await page.locator("#name-error").getByText(ar.common.required).waitFor();
  await page.waitForFunction(() => document.activeElement?.id === "name", undefined, { timeout: 2000 });
  assert.equal(await page.getAttribute("#name", "aria-invalid"), "true");
  await shot("03-add-item-errors-ar");

  await page.fill("#name", "قميص أبيض كلاسيكي");
  await page.fill("#category", "قميص");
  await page.fill("#primaryColor", "أبيض");
  await page.fill("#price", "45,5");
  await page.fill("#currency", "sdg");
  await page.setInputFiles("#photos", { name: "shirt.png", mimeType: "image/png", buffer: PNG });
  await shot("04-add-item-filled-ar");
  await page.click("button[type=submit]");
  await page.waitForURL(/\/ar\/wardrobe$/);

  const card = page.locator(".item-card").filter({ hasText: "قميص أبيض كلاسيكي" });
  await card.waitFor();
  assert.equal(await card.locator(".status-pill").innerText(), ar.status.available);
  const img = card.locator("img");
  await img.waitFor();
  await page.waitForFunction((el) => (el as HTMLImageElement).complete, await img.elementHandle());
  assert.ok((await img.evaluate((el) => (el as HTMLImageElement).naturalWidth)) > 0, "photo loaded from the API");
  await page.getByText("1 قطعة في الدولاب · 1 متاحة للارتداء").waitFor();
  await shot("05-wardrobe-one-item-ar");

  // A reload keeps the session (refresh cookie), with nothing stored in the page.
  await page.reload();
  await card.waitFor();
  assert.ok(await storageIsEmpty());

  // Filters
  await page.getByRole("button", { name: ar.status.laundry }).click();
  await page.getByText(ar.wardrobe.emptyFilter).waitFor();
  assert.equal(await page.getByRole("button", { name: ar.status.laundry }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: ar.wardrobe.all }).click();
  await card.waitFor();
});

test("English: same session, left-to-right, English text", async () => {
  await page.click("a.lang-switch");
  await page.waitForURL(/\/en\/wardrobe$/);
  assert.equal(await page.getAttribute("html", "dir"), "ltr");
  assert.equal(await page.getAttribute("html", "lang"), "en");
  await page.getByRole("heading", { level: 1, name: en.wardrobe.title }).waitFor();
  const card = page.locator(".item-card").first();
  await card.waitFor();
  assert.equal(await card.locator(".status-pill").innerText(), en.status.available);
  await shot("06-wardrobe-en");
});

test("sign out, protected pages send you to sign in, wrong password is refused", async () => {
  await page.getByRole("button", { name: en.nav.signOut }).click();
  await page.waitForURL(/\/en\/sign-in$/);
  await page.getByRole("heading", { level: 1, name: en.auth.signInTitle }).waitFor();
  const cookie = await refreshCookie();
  assert.ok(!cookie || cookie.value === "", "refresh cookie cleared");

  await page.goto(`${WEB}/en/wardrobe/new`);
  await page.waitForURL(/\/en\/sign-in\?next=/);
  await page.getByRole("heading", { level: 1, name: en.auth.signInTitle }).waitFor();

  await page.fill("#email", EMAIL);
  await page.fill("#password", "not-the-password");
  await page.click("button[type=submit]");
  await page.locator(".form-error").getByText(en.auth.wrongCredentials).waitFor();
  await shot("07-sign-in-wrong-en");

  await page.fill("#password", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(/\/en\/wardrobe\/new$/); // back where the visitor was going
});

test("the session routes refuse cross-site calls", async () => {
  const response = await page.request.post(`${WEB}/api/session/refresh`, {
    headers: { Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" },
  });
  assert.equal(response.status(), 403);
});

test("forms post, so credentials never go into a URL even without JavaScript", async () => {
  await page.goto(`${WEB}/en/sign-in`);
  assert.equal(await page.getAttribute("form", "method"), "post");
});

test("no unexpected console errors, script errors or failed loads", () => {
  assert.deepEqual(problems, []);
});
