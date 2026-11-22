import type { ApiClient } from "./client.ts";
import type { Item, ItemInput, ItemPatch, ItemStatus, StatusChange } from "./types.ts";

export type ItemPage = { items: Item[]; total: number };

export async function listItems(
  api: ApiClient,
  filter: { status?: ItemStatus; limit?: number; offset?: number } = {},
): Promise<ItemPage> {
  const { data, headers } = await api.request<Item[]>("GET", "/items", {
    query: { status: filter.status, limit: filter.limit ?? 50, offset: filter.offset ?? 0 },
  });
  // Readable cross-origin only because the API exposes it (CORS expose_headers). If it is ever
  // missing, do not read it as 0.
  const raw = headers.get("X-Total-Count");
  const total = raw === null || raw.trim() === "" ? Number.NaN : Number(raw);
  return { items: data, total: Number.isFinite(total) ? total : data.length };
}

export async function createItem(api: ApiClient, input: ItemInput): Promise<Item> {
  return (await api.request<Item>("POST", "/items", { json: input })).data;
}

export async function getItem(api: ApiClient, id: string): Promise<Item> {
  return (await api.request<Item>("GET", `/items/${encodeURIComponent(id)}`)).data;
}

export async function updateItem(api: ApiClient, id: string, patch: ItemPatch): Promise<Item> {
  return (await api.request<Item>("PATCH", `/items/${encodeURIComponent(id)}`, { json: patch })).data;
}

/** Record a wear (today in the user's time zone unless a day is given). Counts once per day. */
export async function wearItem(api: ApiClient, id: string, wornOn?: string): Promise<Item> {
  return (await api.request<Item>("POST", `/items/${encodeURIComponent(id)}/wear`, { json: wornOn ? { worn_on: wornOn } : {} })).data;
}

export async function itemHistory(api: ApiClient, id: string): Promise<StatusChange[]> {
  return (await api.request<StatusChange[]>("GET", `/items/${encodeURIComponent(id)}/history`)).data;
}
