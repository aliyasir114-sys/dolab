import type { ApiClient } from "./client.ts";
import type { Dashboard, Locale, Profile, User } from "./types.ts";

export async function updateAccount(api: ApiClient, patch: { locale?: Locale; timezone?: string }): Promise<User> {
  return (await api.request<User>("PATCH", "/me", { json: patch })).data;
}

export async function getProfile(api: ApiClient): Promise<Profile> {
  return (await api.request<Profile>("GET", "/me/profile")).data;
}

/** Saving replaces every field: send the whole profile, with null for what is left out. */
export async function saveProfile(api: ApiClient, profile: Profile): Promise<Profile> {
  return (await api.request<Profile>("PUT", "/me/profile", { json: profile })).data;
}

export async function getDashboard(api: ApiClient): Promise<Dashboard> {
  return (await api.request<Dashboard>("GET", "/me/dashboard")).data;
}
