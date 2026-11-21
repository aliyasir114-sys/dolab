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
  const total = Number(headers.get("X-Total-Count"));
  return { items: data, total: Number.isFinite(total) ? total : data.length };
}

export async function createItem(api: ApiClient, input: ItemInput): Promise<Item> {
  return (await api.request<Item>("POST", "/items", { json: input })).data;
}

export async function getItem(api: ApiClient, id: string): Promise<Item> {
  return (await api.request<Item>("GET", `/items/${encodeURIComponent(id)}`)).data;
}
