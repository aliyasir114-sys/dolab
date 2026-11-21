import { CREATABLE_STATUSES, type CreatableStatus, type ItemInput } from "../api/types.ts";
import { MAX_PHOTO_BYTES, MAX_PHOTOS_PER_ITEM, PHOTO_TYPES } from "../api/photos.ts";

/** The form as typed. Mirrors the server's rules (ItemIn in services/api/app/schemas.py). */
export type ItemForm = {
  name: string;
  category: string;
  primaryColor: string;
  status: string;
  purchaseDate: string;
  price: string;
  currency: string;
};

export type ItemFieldError =
  | { field: keyof ItemForm; code: "required" }
  | { field: keyof ItemForm; code: "tooLong"; max: number }
  | { field: "price"; code: "badPrice" }
  | { field: "currency"; code: "badCurrency" | "currencyNeedsPrice" }
  | { field: "purchaseDate"; code: "futureDate" }
  | { field: "status"; code: "required" };

const LIMITS = { name: 120, category: 60, primaryColor: 40 } as const;
// Server: Decimal(max_digits=12, decimal_places=2) and >= 0.
const PRICE = /^\d{1,10}(\.\d{1,2})?$/;

export function validateItem(form: ItemForm, today: string): { errors: ItemFieldError[]; input: ItemInput | null } {
  const errors: ItemFieldError[] = [];
  const name = form.name.trim();
  const category = form.category.trim();
  const color = form.primaryColor.trim();
  const price = form.price.trim().replace(",", ".");
  const currency = form.currency.trim().toUpperCase();

  if (!name) errors.push({ field: "name", code: "required" });
  else if (name.length > LIMITS.name) errors.push({ field: "name", code: "tooLong", max: LIMITS.name });
  if (!category) errors.push({ field: "category", code: "required" });
  else if (category.length > LIMITS.category) errors.push({ field: "category", code: "tooLong", max: LIMITS.category });
  if (color.length > LIMITS.primaryColor) errors.push({ field: "primaryColor", code: "tooLong", max: LIMITS.primaryColor });
  if (!(CREATABLE_STATUSES as readonly string[]).includes(form.status)) errors.push({ field: "status", code: "required" });
  if (price && !PRICE.test(price)) errors.push({ field: "price", code: "badPrice" });
  if (currency && !/^[A-Z]{3}$/.test(currency)) errors.push({ field: "currency", code: "badCurrency" });
  else if (currency && !price) errors.push({ field: "currency", code: "currencyNeedsPrice" });
  if (form.purchaseDate && form.purchaseDate > today) errors.push({ field: "purchaseDate", code: "futureDate" });

  if (errors.length) return { errors, input: null };
  return {
    errors,
    input: {
      name,
      category,
      primary_color: color || null,
      status: form.status as CreatableStatus,
      purchase_date: form.purchaseDate || null,
      purchase_price: price || null,
      currency: currency || null,
    },
  };
}

export type PhotoProblem = { name: string; code: "badPhotoType" | "photoTooBig" } | { code: "tooManyPhotos" };

export function checkPhotos(files: { name: string; type: string; size: number }[]): PhotoProblem[] {
  const problems: PhotoProblem[] = [];
  if (files.length > MAX_PHOTOS_PER_ITEM) problems.push({ code: "tooManyPhotos" });
  for (const file of files) {
    if (!(PHOTO_TYPES as readonly string[]).includes(file.type)) problems.push({ name: file.name, code: "badPhotoType" });
    else if (file.size > MAX_PHOTO_BYTES) problems.push({ name: file.name, code: "photoTooBig" });
  }
  return problems;
}

/** The same simple check the browser's type="email" does; the server decides. */
export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

export const MIN_PASSWORD = 8;
export const MAX_PASSWORD = 128;
