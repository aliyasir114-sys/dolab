import type { ApiClient } from "./client.ts";

/**
 * Export and deletion, behind an interface because step C changes their contracts (deletion log,
 * intents, storage clean-up). Screens use DataLifecycle only; swap the implementation when C's
 * contract is settled (Ali, 2026-11-21).
 */
export type DataLifecycle = {
  /** Everything DOLAB keeps about the user, as a JSON file to save. */
  exportAccount(): Promise<Blob>;
  /** Remove the optional body details. */
  clearProfile(): Promise<void>;
  deleteItem(itemId: string): Promise<void>;
  /** Delete the account and everything it owns. The caller then ends the web session. */
  deleteAccount(): Promise<void>;
};

/** The current server: immediate deletion (PRD A6), export as one JSON document. */
export function serverLifecycle(api: ApiClient): DataLifecycle {
  return {
    async exportAccount() {
      const { data } = await api.request<unknown>("GET", "/me/export");
      return new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    },
    async clearProfile() {
      await api.request<void>("DELETE", "/me/profile");
    },
    async deleteItem(itemId) {
      await api.request<void>("DELETE", `/items/${encodeURIComponent(itemId)}`);
    },
    async deleteAccount() {
      await api.request<void>("DELETE", "/me");
    },
  };
}
