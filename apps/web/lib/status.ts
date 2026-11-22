import type { ItemStatus } from "./api/types.ts";

/**
 * The statuses a person can move an item to from the item page (PATCH), mirroring the server's
 * table (services/api/app/transitions.py, plan 4.9). The server stays the authority: a 409 is
 * still shown if they ever differ.
 *   available, laundry, repair, stored -> any other of these, or lost
 *   lost -> available
 *   donated, for_sale -> none here (a donation is cancelled, a listing ended, elsewhere)
 */
const EDITABLE: ItemStatus[] = ["available", "laundry", "repair", "stored"];

export function statusTargets(from: ItemStatus): ItemStatus[] {
  if (EDITABLE.includes(from)) return [...EDITABLE.filter((s) => s !== from), "lost"];
  if (from === "lost") return ["available"];
  return [];
}

/** Only available items can be worn (the server refuses others with 409). */
export function canWear(status: ItemStatus): boolean {
  return status === "available";
}
