/**
 * Types of the current API (services/api/app/schemas.py), written by hand.
 * Replaced by the generated client after step E; nothing outside lib/api should depend on how
 * these are produced.
 */
export type Locale = "ar" | "en";

export const ITEM_STATUSES = ["available", "laundry", "repair", "stored", "for_sale", "donated", "lost"] as const;
export type ItemStatus = (typeof ITEM_STATUSES)[number];

/** Statuses an item may be created with (for_sale and donated come from the market and donations). */
export const CREATABLE_STATUSES = ["available", "laundry", "repair", "stored", "lost"] as const;
export type CreatableStatus = (typeof CREATABLE_STATUSES)[number];

export type User = {
  id: string;
  email: string;
  locale: Locale;
  timezone: string;
  account_type: string;
};

export type RegisterInput = {
  email: string;
  password: string;
  locale: Locale;
  timezone: string;
};

export type Item = {
  id: string;
  name: string;
  category: string;
  primary_color: string | null;
  secondary_colors: string[];
  color_confidence: number | null;
  color_confirmed: boolean;
  status: ItemStatus;
  purchase_date: string | null;
  /** A decimal sent as a string, e.g. "45.50". */
  purchase_price: string | null;
  currency: string | null;
  added_at: string;
  updated_at: string;
  wear_count: number;
  last_worn_at: string | null;
  /** Relative signed path; resolve with mediaUrl(). Expires after a few minutes. */
  cover_thumb_url: string | null;
};

export type ItemInput = {
  name: string;
  category: string;
  primary_color?: string | null;
  secondary_colors?: string[];
  color_confirmed?: boolean;
  status?: CreatableStatus;
  purchase_date?: string | null;
  purchase_price?: string | null;
  currency?: string | null;
};

export type Photo = {
  id: string;
  content_type: string;
  width: number;
  height: number;
  size_bytes: number;
  created_at: string;
  url: string;
  thumb_url: string;
};

/** Session answer from the web's own session routes: the refresh token never reaches the page. */
export type AccessToken = {
  access_token: string;
  expires_in: number;
};

/** Fields a PATCH may change. Status changes follow the server's transition table (lib/status.ts). */
export type ItemPatch = Partial<Pick<Item, "name" | "category" | "primary_color" | "status" | "purchase_date" | "purchase_price" | "currency">>;

export type StatusChange = {
  from_status: ItemStatus | null;
  to_status: ItemStatus;
  /** created, user, donation, donation_cancelled, listing */
  reason: string;
  ref_id: string | null;
  changed_at: string;
};

/** Body details; every field optional (PRD 4.4). Saving replaces all of them. */
export type Profile = {
  height_cm: number | null;
  weight_kg: number | null;
  usual_size: string | null;
  body_shape: string | null;
  face_shape: string | null;
};

export type Dashboard = {
  total_items: number;
  /** Items not donated or lost. */
  active_items: number;
  by_status: Partial<Record<ItemStatus, number>>;
  colors: Record<string, number>;
  /** Active items worn at least once this month (in the user's time zone). */
  worn_this_month: number;
  usage_ratio: number | null;
};
