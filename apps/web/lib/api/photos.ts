import type { ApiClient } from "./client.ts";
import type { Photo } from "./types.ts";

/**
 * Photo storage, behind an interface because step C changes it (storage and deletion contracts).
 * Screens depend on PhotoStore only; swap the implementation when C's contract is settled.
 */
export type PhotoStore = {
  upload(itemId: string, file: Blob, fileName: string): Promise<Photo>;
  list(itemId: string): Promise<Photo[]>;
  remove(itemId: string, photoId: string): Promise<void>;
};

export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const MAX_PHOTO_BYTES = 8 * 1024 * 1024;
export const MAX_PHOTOS_PER_ITEM = 6;

/** The current server: photos go to /v1/items/{id}/photos and come back as signed links. */
export function serverPhotoStore(api: ApiClient): PhotoStore {
  const path = (itemId: string) => `/items/${encodeURIComponent(itemId)}/photos`;
  return {
    async upload(itemId, file, fileName) {
      const form = new FormData();
      form.append("file", file, fileName);
      return (await api.request<Photo>("POST", path(itemId), { form })).data;
    },
    async list(itemId) {
      return (await api.request<Photo[]>("GET", path(itemId))).data;
    },
    async remove(itemId, photoId) {
      await api.request<void>("DELETE", `${path(itemId)}/${encodeURIComponent(photoId)}`);
    },
  };
}
