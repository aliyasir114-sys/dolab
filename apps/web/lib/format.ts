import { plural, type AppLocale, type PluralForms } from "./i18n/messages.ts";

/** Western digits in both languages, as everywhere else in DOLAB. */
function intlLocale(locale: AppLocale): string {
  return locale === "ar" ? "ar-u-nu-latn" : "en";
}

/** "2026-03-05" -> "5 مارس 2026" / "March 5, 2026". Dates without a time are shown as written. */
export function formatDay(day: string, locale: AppLocale): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(intlLocale(locale), { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

export function formatMoment(iso: string, locale: AppLocale): string {
  return new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

function dayNumber(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

export function daysBetween(from: string, to: string): number {
  return Math.round(dayNumber(to) - dayNumber(from));
}

/** Whole months from one day to another (not negative). */
export function monthsBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  const months = (ty - fy) * 12 + (tm - fm) - (td < fd ? 1 : 0);
  return Math.max(0, months);
}

export type AgoForms = { today: string; yesterday: string; days: PluralForms };

/** "اليوم" / "أمس" / "قبل 3 أيام"… */
export function daysAgo(day: string, today: string, forms: AgoForms, locale: AppLocale): string {
  const n = daysBetween(day, today);
  if (n <= 0) return forms.today;
  if (n === 1) return forms.yesterday;
  return plural(forms.days, n, locale);
}

export function formatPrice(amount: string, currency: string | null, locale: AppLocale): string {
  const value = Number(amount);
  if (!currency) return new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: 2 }).format(value);
  try {
    return new Intl.NumberFormat(intlLocale(locale), { style: "currency", currency, currencyDisplay: "code" }).format(value);
  } catch {
    return `${value} ${currency}`;
  }
}

/** A short reference shown on the item card, like the design's DLB-000124. */
export function itemRef(id: string): string {
  return `DLB-${id.replace(/-/g, "").slice(0, 6).toUpperCase()}`;
}

/** Today as YYYY-MM-DD in a time zone (the account's, so it matches the server's "today"). */
export function todayIn(timeZone?: string, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  } catch {
    return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  }
}
