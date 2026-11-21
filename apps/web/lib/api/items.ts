import type { ApiClient } from "./client.ts";
import type { Item, ItemInput, ItemStatus } from "./types.ts";

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
