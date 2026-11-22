/**
 * End-to-end check of step F part 2 (item page, profile and settings, dashboard) against the real
 * API, in Chromium. Its own account, so it does not depend on smoke.test.ts.
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import { ar, en } from "../lib/i18n/messages.ts";

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const SHOTS = process.env.SHOTS_DIR ?? "e2e/shots";
const EMAIL = `e2e-f2-${Date.now()}@example.com`;
const PASSWORD = "a-long-test-password";
const NEW_PASSWORD = "another-long-password";

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAIAAABvFaqvAAAAPUlEQVR4XmP8+vUrAzUAEzUMAYHBZxALpteEhYUJ+vft27doagaf10ZdRBiMhhFhMBpGhAHVwohxtMwmCAAUwAjvo9jZ5AAAAABJRU5ErkJggg==",
  "base64",
);

let browser: Browser;
let context: BrowserContext;
let page: Page;
let itemPath = ""; // "/wardrobe/<id>"
const problems: string[] = [];
// Refusals the scenario causes on purpose: a wrong current password (403); the page of a deleted
// item (404); a refresh or sign-in after the account is deleted (401).
const EXPECTED = [/status of 401/, /status of 403/, /status of 404/];
const ABORTED = "net::ERR_ABORTED";

before(async () => {
  mkdirSync(SHOTS, { recursive: true });
  browser = await chromium.launch();
  context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
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
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

function monthsAgo(n: number): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - n);
  return d.toISOString().slice(0, 10);
}

async function addItem(name: string, category: string, color: string, extra: { date?: string; price?: string; photo?: boolean } = {}) {
  await page.goto(`${WEB}/ar/wardrobe/new`);
  await page.getByRole("heading", { level: 1, name: ar.item.addTitle }).waitFor();
  await page.fill("#name", name);
  await page.fill("#category", category);
  await page.fill("#primaryColor", color);
  if (extra.date) await page.fill("#purchaseDate", extra.date);
  if (extra.price) {
    await page.fill("#price", extra.price);
    await page.fill("#currency", "SDG");
  }
  if (extra.photo) await page.setInputFiles("#photos", { name: "item.png", mimeType: "image/png", buffer: PNG });
  await page.click("button[type=submit]");
  await page.waitForURL(/\/ar\/wardrobe$/);
}

test("item page: details, wear, status, photos, edit (Arabic)", async () => {
  await page.goto(`${WEB}/ar/sign-up`);
  await page.getByRole("heading", { level: 1, name: ar.auth.signUpTitle }).waitFor();
  await page.fill("#email", EMAIL);
  await page.fill("#password", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(/\/ar\/wardrobe$/);

  await addItem("قميص أبيض كلاسيكي", "قميص", "أبيض", { date: monthsAgo(8), price: "45", photo: true });
  await addItem("بنطلون أسود", "بنطلون", "أسود");

  // The nav shows the three sections; the wardrobe is the current page.
  const nav = page.getByRole("navigation", { name: ar.nav.main });
  for (const label of [ar.nav.wardrobe, ar.nav.dashboard, ar.nav.profile]) await nav.getByRole("link", { name: label }).waitFor();
  assert.equal(await nav.getByRole("link", { name: ar.nav.wardrobe }).getAttribute("aria-current"), "page");

  // A card opens its item page.
  await page.locator(".item-link").filter({ hasText: "قميص أبيض كلاسيكي" }).click();
  await page.waitForURL(/\/ar\/wardrobe\/[^/]+$/);
  itemPath = new URL(page.url()).pathname.replace(/^\/ar/, "");
  await page.getByRole("heading", { level: 1, name: "قميص أبيض كلاسيكي" }).waitFor();
  assert.equal(await nav.getByRole("link", { name: ar.nav.wardrobe }).getAttribute("aria-current"), "true");
  await page.getByText(/^DLB-[0-9A-F]{6}$/).waitFor();
  const details = page.locator(".details");
  await details.getByText(ar.itemPage.neverWorn).waitFor();
  await details.getByText(/^(7|8) أشهر$/).waitFor();
  const photo = page.locator(".gallery-main img");
  await photo.waitFor();
  await page.waitForFunction((el) => (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0, await photo.elementHandle());

  // Two columns, photos 45% and data 55%, within 1200px (Gemini, 2026-11-22).
  const [left, right] = await page.locator(".item-gallery, .item-info").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width));
  const ratio = left / (left + right);
  assert.ok(ratio > 0.43 && ratio < 0.47, `gallery share ${ratio}`);
  assert.ok(left + right <= 1200, String(left + right));
  await shot("20-item-ar");

  // Wear today.
  await page.getByRole("button", { name: ar.itemPage.wearToday }).click();
  await page.getByRole("status").getByText(ar.itemPage.wornToday).waitFor();
  await details.getByText(ar.itemPage.today).waitFor();
  assert.equal(await details.locator("div").filter({ hasText: ar.itemPage.wearCount }).locator("dd").innerText(), "1");

  // Status: to laundry, then the wear button gives way; the history records it; then back.
  await page.selectOption("#next-status", "laundry");
  await page.getByRole("button", { name: ar.itemPage.applyStatus }).click();
  await page.getByRole("status").getByText(ar.itemPage.statusChanged).waitFor();
  assert.equal(await page.locator(".item-heading .status-pill").innerText(), ar.status.laundry);
  await page.getByText(ar.itemPage.cannotWear).waitFor();
  await page.locator(".history").getByText(`من «${ar.status.available}» إلى «${ar.status.laundry}»`).waitFor();
  const options = await page.locator("#next-status option").evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
  assert.deepEqual(options, ["", "available", "repair", "stored", "lost"]);
  await page.selectOption("#next-status", "available");
  await page.getByRole("button", { name: ar.itemPage.applyStatus }).click();
  await page.getByRole("button", { name: ar.itemPage.wearToday }).waitFor();

  // A second photo: thumbnails appear and switch the large photo.
  await page.setInputFiles("#add-photos", { name: "second.png", mimeType: "image/png", buffer: PNG });
  await page.getByRole("status").getByText(ar.itemPage.photoAdded).waitFor();
  const thumbs = page.locator(".thumb");
  assert.equal(await thumbs.count(), 2);
  assert.equal(await thumbs.nth(1).getAttribute("aria-pressed"), "true");
  await thumbs.nth(0).click();
  assert.equal(await thumbs.nth(0).getAttribute("aria-pressed"), "true");

  // The delete-photo dialog: focus starts on Cancel, the page behind is inert and does not
  // scroll, Escape closes it and focus goes back to the button that opened it.
  const opener = page.getByRole("button", { name: ar.itemPage.deletePhoto });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: ar.itemPage.deletePhotoTitle });
  await dialog.waitFor();
  await page.waitForFunction(() => document.activeElement?.textContent === "إلغاء");
  assert.equal(await page.locator(".shell").evaluate((el) => (el as HTMLElement).inert), true);
  assert.ok(await page.evaluate(() => document.documentElement.classList.contains("is-scroll-locked")));
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  assert.ok(await dialog.evaluate((el) => el.contains(document.activeElement)), "Tab stays in the dialog");
  await shot("21-item-dialog-ar");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  assert.equal(await opener.evaluate((el) => el === document.activeElement), true);
  assert.equal(await page.locator(".shell").evaluate((el) => (el as HTMLElement).inert), false);

  // Delete it for real.
  await opener.click();
  await dialog.getByRole("button", { name: ar.itemPage.deletePhotoConfirm }).click();
  await dialog.waitFor({ state: "detached" });
  assert.equal(await thumbs.count(), 0);

  // Edit: an empty name is refused at once; a new name is saved.
  await page.getByRole("button", { name: ar.itemPage.edit }).click();
  await page.fill("#name", "");
  await page.getByRole("button", { name: ar.itemPage.saveChanges }).click();
  await page.locator("#name-error").getByText(ar.common.required).waitFor();
  await page.fill("#name", "قميص أوكسفورد أبيض");
  await page.getByRole("button", { name: ar.itemPage.saveChanges }).click();
  await page.getByRole("heading", { level: 1, name: "قميص أوكسفورد أبيض" }).waitFor();
  await page.getByRole("status").getByText(ar.itemPage.edited).waitFor();
  await shot("22-item-after-ar");
});

test("dashboard: usage, status grid of up to 4 per row, colours (Arabic)", async () => {
  await page.getByRole("navigation", { name: ar.nav.main }).getByRole("link", { name: ar.nav.dashboard }).click();
  await page.waitForURL(/\/ar\/dashboard$/);
  await page.getByRole("heading", { level: 1, name: ar.dashboard.title }).waitFor();
  await page.getByText("ارتديت 1 من قطعتين هذا الشهر").waitFor();
  assert.equal(await page.locator(".usage-bar").getAttribute("aria-label"), "50% من القطع النشطة ارتُديت هذا الشهر");
  const cards = page.locator(".stat-card");
  assert.equal(await cards.count(), 5);
  const tops = await cards.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
  assert.equal(tops.filter((top) => top === tops[0]).length, 4, `4 cards on the first row: ${tops}`);
  assert.equal(await cards.filter({ hasText: ar.status.available }).locator(".stat-value").innerText(), "2");
  await page.locator(".color-list").getByText("أسود").waitFor();
  await page.locator(".color-list").getByText("أبيض").waitFor();
  await shot("23-dashboard-ar");
});

test("profile: measurements, settings, password, export, clear (Arabic)", async () => {
  await page.getByRole("navigation", { name: ar.nav.main }).getByRole("link", { name: ar.nav.profile }).click();
  await page.waitForURL(/\/ar\/profile$/);
  await page.getByRole("heading", { level: 1, name: ar.profile.title }).waitFor();
  const width = await page.locator(".profile").evaluate((el) => el.getBoundingClientRect().width);
  assert.ok(width <= 680, String(width));
  // Height and weight share a row.
  const tops = await page.locator("#height, #weight").evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
  assert.equal(new Set(tops).size, 1, String(tops));
  await shot("24-profile-ar");

  // Out of range is refused with the range, and focus goes to the field.
  await page.fill("#height", "20");
  await page.getByRole("button", { name: ar.profile.saveMeasurements }).click();
  await page.locator("#height-error").getByText(ar.profile.heightRange).waitFor();
  await page.waitForFunction(() => document.activeElement?.id === "height");

  await page.fill("#height", "180");
  await page.fill("#weight", "75,5");
  await page.locator(".chips").filter({ hasText: ar.profile.usualSize }).getByText("M", { exact: true }).click();
  await page.locator(".chips").filter({ hasText: ar.profile.bodyShape }).getByText(ar.profile.bodyShapes.rectangle).click();
  await page.locator(".chips").filter({ hasText: ar.profile.faceShape }).getByText(ar.profile.faceShapes.oval).click();
  await page.getByRole("button", { name: ar.profile.saveMeasurements }).click();
  await page.locator(".profile-section").first().getByRole("status").getByText(ar.profile.saved).waitFor();
  await page.reload();
  await page.getByRole("heading", { level: 1, name: ar.profile.title }).waitFor();
  assert.equal(await page.inputValue("#height"), "180");
  assert.equal(await page.inputValue("#weight"), "75.5");
  assert.equal(await page.locator('input[name="size"][value="M"]').isChecked(), true);
  assert.equal(await page.locator('input[name="body"][value="rectangle"]').isChecked(), true);

  // Settings: time zone.
  await page.selectOption("#timezone", "Africa/Khartoum");
  await page.getByRole("button", { name: ar.profile.saveSettings }).click();
  await page.locator(".profile-section").nth(1).getByRole("status").getByText(ar.profile.saved).first().waitFor();

  // Password: a wrong current password is refused; the right one changes it and keeps the session.
  await page.fill("#current-password", "not-the-password");
  await page.fill("#new-password", NEW_PASSWORD);
  await page.getByRole("button", { name: ar.profile.changePassword }).click();
  await page.getByText(ar.profile.wrongCurrent).waitFor();
  await page.fill("#current-password", PASSWORD);
  await page.getByRole("button", { name: ar.profile.changePassword }).click();
  await page.getByText(ar.profile.passwordChanged).waitFor();
  const cookie = (await context.cookies()).find((c) => c.name === "__Host-dolab_rt");
  assert.ok(cookie?.value, "a new refresh cookie");
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.reload();
  await page.getByRole("heading", { level: 1, name: ar.profile.title }).waitFor(); // still signed in
  assert.equal(await page.inputValue("#timezone"), "Africa/Khartoum");

  // Export: a JSON file with the account and both items.
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: ar.profile.exportButton }).click()]);
  assert.match(download.suggestedFilename(), /^dolab-export-\d{4}-\d{2}-\d{2}\.json$/);
  const data = JSON.parse(readFileSync(await download.path(), "utf8"));
  assert.equal(data.user.email, EMAIL);
  assert.equal(data.items.length, 2);
  assert.equal(data.profile.height_cm, 180);

  // Clear measurements through the dialog.
  await page.getByRole("button", { name: ar.profile.clearProfile }).click();
  const dialog = page.getByRole("dialog", { name: ar.profile.clearProfileTitle });
  await dialog.getByRole("button", { name: ar.profile.clearProfileConfirm }).click();
  await dialog.waitFor({ state: "detached" });
  await page.getByText(ar.profile.cleared).waitFor();
  assert.equal(await page.inputValue("#height"), "");
});

test("English and phone screens for design review", async () => {
  for (const [name, path, title] of [
    ["25-item-en", `/en${itemPath}`, "قميص أوكسفورد أبيض"],
    ["26-dashboard-en", "/en/dashboard", en.dashboard.title],
    ["27-profile-en", "/en/profile", en.profile.title],
  ] as Array<[string, string, string]>) {
    await page.goto(`${WEB}${path}`);
    await page.getByRole("heading", { level: 1, name: title }).waitFor();
    assert.equal(await page.getAttribute("html", "dir"), "ltr");
    await shot(name);
  }
  await page.goto(`${WEB}/en/dashboard`);
  await page.getByText("You wore 1 of 2 items this month").waitFor();

  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, path, title] of [
    ["28-item-ar-phone", `/ar${itemPath}`, "قميص أوكسفورد أبيض"],
    ["29-dashboard-ar-phone", "/ar/dashboard", ar.dashboard.title],
    ["30-profile-ar-phone", "/ar/profile", ar.profile.title],
    ["31-item-en-phone", `/en${itemPath}`, "قميص أوكسفورد أبيض"],
    ["32-dashboard-en-phone", "/en/dashboard", en.dashboard.title],
    ["33-profile-en-phone", "/en/profile", en.profile.title],
  ] as Array<[string, string, string]>) {
    await page.goto(`${WEB}${path}`);
    await page.getByRole("heading", { level: 1, name: title }).waitFor();
    // Nothing wider than the screen.
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${name} scrolls sideways`);
    await shot(name);
  }
  // On the phone the item page is one column, photos first.
  await page.goto(`${WEB}/ar${itemPath}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  const [gallery, info] = await page.locator(".item-gallery, .item-info").evaluateAll((els) => els.map((el) => el.getBoundingClientRect()));
  assert.ok(info.top >= gallery.bottom, "data below photos");
  // The dialog on the phone, buttons full width.
  await page.getByRole("button", { name: ar.itemPage.deleteItem }).click();
  await page.getByRole("dialog", { name: ar.itemPage.deleteTitle }).waitFor();
  await shot("34-item-delete-dialog-ar-phone");
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1280, height: 900 });
});

test("delete the item, then the account", async () => {
  await page.goto(`${WEB}/en${itemPath}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.getByRole("button", { name: en.itemPage.deleteItem }).click();
  await page.getByRole("dialog", { name: en.itemPage.deleteTitle }).getByRole("button", { name: en.itemPage.deleteConfirm }).click();
  await page.waitForURL(/\/en\/wardrobe$/);
  await page.getByText("1 item in the wardrobe · 1 ready to wear").waitFor();

  // A deleted item's page says so.
  await page.goto(`${WEB}/en${itemPath}`);
  await page.getByText(en.itemPage.notFound).waitFor();

  await page.goto(`${WEB}/en/profile`);
  await page.getByRole("heading", { level: 1, name: en.profile.title }).waitFor();
  await page.getByRole("button", { name: en.profile.deleteAccount }).click();
  await page.getByRole("dialog", { name: en.profile.deleteAccountTitle }).getByRole("button", { name: en.profile.deleteAccountConfirm }).click();
  await page.waitForURL(/\/en\/sign-in$/);
  const cookie = (await context.cookies()).find((c) => c.name === "__Host-dolab_rt");
  assert.ok(!cookie || cookie.value === "", "refresh cookie cleared");
  await page.fill("#email", EMAIL);
  await page.fill("#password", NEW_PASSWORD);
  await page.click("button[type=submit]");
  await page.locator(".form-error").getByText(en.auth.wrongCredentials).waitFor();
});

test("no unexpected console errors, script errors or failed loads", () => {
  assert.deepEqual(problems, []);
});
